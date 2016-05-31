# coding=utf-8
from __future__ import division
import copy
import ujson
import os
import math
from webob import Response
from webob.static import DirectoryApp
from operator import attrgetter
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ipv4
from ryu.lib.packet import arp
from ryu.topology import event
from ryu.topology.api import get_switch, get_link
from ryu.lib import dpid as dpid_lib
from ryu.app.wsgi import ControllerBase, WSGIApplication, route, websocket, WebSocketRPCClient
from socket import error as SocketError
from ryu.contrib.tinyrpc.exc import InvalidReplyError
import flow_maintain
import load_forecast

SLEEP_PERIOD = 10
IS_UPDATE = True
PATH = os.path.dirname(__file__)


class TopoMonitor(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _NAME = 'TopoMonitor'
    _CONTEXTS = {
        'wsgi': WSGIApplication,
        "FlowMaintain": flow_maintain.FlowTableMaintain
    }

    def __init__(self, *args, **kwargs):
        super(TopoMonitor, self).__init__(*args, **kwargs)
        wsgi = kwargs['wsgi']
        wsgi.register(TopoMonitorController, {'dc_network': self})
        self.dc_network = self
        self.flow_cacl = kwargs["FlowMaintain"]
        self.forecast = load_forecast.Forecast()

        self.port_stats = {}
        self.port_speed = {}
        # {"port":{dpid:{port:body,..},..},"flow":{dpid:body,..}
        self.stats = {}
        self.port_link = {}  # {dpid:{port_no:(config,state,cur),..},..}
        self.switch_speed = {}
        self.history_speed = {}

        # { dpid: datapath, ...}
        self.datapaths = {}
        self.switch_list = {}
        self.link_to_port = {}
        self.access_table = {}
        self.switch_port_table = {}
        self.switch_port_down = {}
        # [ dpid1, dpid2, ... ]
        self.switches = []
        # { dpid: (1,2,3), ... }
        self.access_ports = {}
        # { dpid: (11, 12), ... }
        self.tunnel_ports = {}
        # { vni:[1,2,3]}
        self.vni_switch_list = {100: [1, 2, 3], 200: [1, 2, 3]}

        # Topology Graph
        self.graph = {}
        self.pre_link_to_port = {}
        self.pre_graph = {}
        self.pre_access_table = {}

        # Save all websocket clients
        self.rpc_clients = []

        # create green threads to collect topology
        self.monitor_thread = hub.spawn(self._monitor)
        self.discover_thread = hub.spawn(self._discover)
        self.flow_thread = hub.spawn(self._flow)

    # show topo ,and get topo again
    def _discover(self):
        while True:
            hub.sleep(18)
            self.get_topology(None)
            self.show_topology()

    def _monitor(self):
        while True:
            self.stats['flow'] = {}
            self.stats['port'] = {}
            for dp in self.datapaths.values():
                self.port_link.setdefault(dp.id, {})
                self.switch_speed.setdefault(dp.id, [])
                self.history_speed.setdefault(dp.id, [])

                if len(self.switch_speed[dp.id]) == 12:
                    print ("Dump switch_speed -> history_speed")
                    temp_speedlist = [self.switch_speed[dp.id][i:i + 6]
                                      for i in range(0, len(self.switch_speed[dp.id]), 6)]
                    self.history_speed[dp.id].append(temp_speedlist[0])
                    if len(self.history_speed[dp.id]) > 10:
                        self.history_speed[dp.id].pop()
                    self.switch_speed[dp.id] = temp_speedlist[1]
                self.switch_speed[dp.id].append(0)
                # send portstatus request to all datapaths
                self._request_stats(dp)
                self._ws_broadcast("updateSpeed", self.port_speed)

            hub.sleep(SLEEP_PERIOD)

    def _flow(self):
        while True:
            hub.sleep(20)
            self.flow_cacl.switch_calc(self.datapaths, self.access_table,
                                       self.vni_switch_list, self.tunnel_ports)
            hub.sleep(20)

    events = [event.EventSwitchEnter, event.EventSwitchLeave, event.EventPortAdd,
              event.EventPortDelete, event.EventLinkAdd, event.EventLinkDelete]
    # event.EventPortModify

    # Collect topology information
    @set_ev_cls(events)
    def get_topology(self, ev):
        # reset topology data when topology changed
        self.switch_port_table = {}
        self.switch_port_down = {}
        self.tunnel_ports = {}
        self.access_ports = {}
        self.switch_port_down = {}
        self.switch_list = get_switch(self.dc_network, None)
        # know every datapath has what ports
        self.create_port_map(self.switch_list)
        # get all switched dpid -> switches=[]
        self.switches = self.switch_port_table.keys()

        links = get_link(self.dc_network, None)
        self.create_tunnel_links(links)
        self.create_access_ports()
        self.get_graph(self.link_to_port.keys())
        print "Refresh Topology because:" + str(ev)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        self.switch_list = get_switch(self.dc_network, None)
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.debug('A new datapath register: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.debug('A datapath unregister: %016x', datapath.id)
                del self.datapaths[datapath.id]
                # send alert msg to web
                error_msg = {"type": 1, "dpid": datapath.id}
                self._ws_broadcast("errorLog", error_msg)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        reason = msg.reason
        port_no = msg.desc.port_no
        dpid = datapath.id
        ofproto = datapath.ofproto
        # state: 1 is down, 0 is up
        state = msg.desc.state

        reason_dict = {ofproto.OFPPR_ADD: "is added!",
                       ofproto.OFPPR_DELETE: "is deleted!",
                       ofproto.OFPPR_MODIFY: "is modified!"}

        if reason in reason_dict:
            print ("Switch%d: port %s %s, state is %d" % (dpid, port_no, reason_dict[reason], state))
        else:
            print ("Switch%d: illegal port state %s %s, state is %d" % (dpid, port_no, reason, state))

        if reason == ofproto.OFPPR_ADD or reason == ofproto.OFPPR_DELETE:
            return

        # define which status could trigger vm auto migrate
        if state == 1 and dpid == 1 and reason == ofproto.OFPPR_MODIFY:
            # send alert msg to web
            error_msg = {"type": 2, "dpid": datapath.id, "port": port_no}
            self._ws_broadcast("errorLog", error_msg)
            # trigger vm migrate, destination is predicted
            self.auto_migrate(dpid, port_no)
            # clear the down port real speed record
            self.port_speed[dpid][port_no][2] = 0

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        self.stats['port'][dpid] = body

        # print ("Get DP%d 's PortStatsReply" % ev.msg.datapath.id)

        for stat in sorted(body, key=attrgetter('port_no')):
            if stat.port_no != ofproto_v1_3.OFPP_LOCAL:
                key = (dpid, stat.port_no)
                value = (stat.tx_bytes, stat.rx_bytes, stat.rx_errors,
                         stat.duration_sec, stat.duration_nsec)

                self._save_stats(self.port_stats, key, value, 5)

                # Get port speed.
                pre = 0
                period = SLEEP_PERIOD
                tmp = self.port_stats[key]
                if len(tmp) > 1:
                    # receive+send count in last time
                    pre = tmp[-2][0] + tmp[-2][1]
                    # time interval
                    period = self._get_period(tmp[-1][3], tmp[-1][4],
                                              tmp[-2][3], tmp[-2][4])

                speed = self._get_speed(
                    self.port_stats[key][-1][0] + self.port_stats[key][-1][1],
                    pre, period)
                # self._save_stats(self.port_speed, key, speed, 5)
                self.port_speed.setdefault(dpid, {})
                trans_num = math.ceil((stat.tx_bytes + stat.rx_bytes)/1024)
                err_num = math.ceil(stat.rx_errors/1024)
                self.port_speed[dpid][stat.port_no] = [trans_num, err_num, speed]
                self.switch_speed[dpid][-1] += speed

                # self.switch_speed[dpid].pop(0)
                # print ("Get DP%d Port%d's PortStatsReply, it's speed is %d" % (ev.msg.datapath.id, stat.port_no, speed))

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def port_desc_stats_reply_handler(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        ofproto = msg.datapath.ofproto

        config_dist = {ofproto.OFPPC_PORT_DOWN: "Down",
                       ofproto.OFPPC_NO_RECV: "No Recv",
                       ofproto.OFPPC_NO_FWD: "No Farward",
                       ofproto.OFPPC_NO_PACKET_IN: "No Packet-in"}

        state_dist = {ofproto.OFPPS_LINK_DOWN: "Down",
                      ofproto.OFPPS_BLOCKED: "Blocked",
                      ofproto.OFPPS_LIVE: "Live"}

        ports = []
        for p in ev.msg.body:
            ports.append('port_no=%d hw_addr=%s name=%s config=0x%08x '
                         'state=0x%08x curr=0x%08x advertised=0x%08x '
                         'supported=0x%08x peer=0x%08x curr_speed=%d '
                         'max_speed=%d' %
                         (p.port_no, p.hw_addr,
                          p.name, p.config,
                          p.state, p.curr, p.advertised,
                          p.supported, p.peer, p.curr_speed,
                          p.max_speed))

            if p.config in config_dist:
                config = config_dist[p.config]
            else:
                config = "up"

            if p.state in state_dist:
                state = state_dist[p.state]
            else:
                state = "up"

            port_feature = (config, state, p.curr_speed)
            self.port_link[dpid][p.port_no] = port_feature
            # self.logger.debug('OFPPortDescStatsReply received: %s', ports)
            # print "Get EventOFPPortDescStatsReply:" + str(ports)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        vni = msg.match.get("tunnel_id")
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        eth_type = pkt.get_protocols(ethernet.ethernet)[0].ethertype
        arp_pkt = pkt.get_protocol(arp.arp)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        if arp_pkt and vni and in_port < 10:
            print "Get an ARP packet, vni: %s , dpid: %s" % (str(vni), str(datapath.id))
            arp_src_ip = arp_pkt.src_ip
            arp_dst_ip = arp_pkt.dst_ip
            src_mac = arp_pkt.src_mac

            # first time learn the vm
            if not self.access_table.get((datapath.id, in_port, vni)):
                # record the access info
                self.register_access_info(datapath.id, in_port, arp_src_ip, vni, src_mac)
                self.update_vxlan_group(datapath.id, vni)
                # put flow entry for this vm
                match = parser.OFPMatch(tunnel_id=vni, eth_dst=src_mac)
                actions = [parser.OFPActionOutput(in_port)]
                self.flow_cacl.add_flow_table(datapath, 10, match, actions, table_id=1)

                for vni_dpid in self.vni_switch_list[vni]:
                    if vni_dpid != datapath.id:
                        match = parser.OFPMatch(tunnel_id=vni, eth_dst=src_mac)
                        actions = [parser.OFPActionOutput(datapath.id + 10)]
                        self.flow_cacl.add_flow_table(self.datapaths[vni_dpid], 100, match, actions, table_id=2)

            if arp_pkt.opcode == arp.ARP_REQUEST:
                arp_src_ip = arp_pkt.src_ip
                arp_dst_ip = arp_pkt.dst_ip
                # ARP proxy response
                dst_host_mac = self.get_host_mac(arp_dst_ip)
                if dst_host_mac:
                    actions = [parser.OFPActionOutput(in_port)]
                    ARP_Reply = packet.Packet()

                    ARP_Reply.add_protocol(ethernet.ethernet(
                        ethertype=eth.ethertype,
                        dst=eth.src,
                        src=dst_host_mac))
                    ARP_Reply.add_protocol(arp.arp(
                        opcode=arp.ARP_REPLY,
                        src_mac=dst_host_mac,
                        src_ip=arp_dst_ip,
                        dst_mac=eth.src,
                        dst_ip=arp_src_ip))

                    ARP_Reply.serialize()

                    out = parser.OFPPacketOut(
                        datapath=datapath,
                        buffer_id=ofproto.OFP_NO_BUFFER,
                        in_port=ofproto.OFPP_CONTROLLER,
                        actions=actions, data=ARP_Reply.data)
                    datapath.send_msg(out)
                    print "ARP_Reply"
                    return True

    # Active VM migration strategy
    def auto_migrate(self, dpid, port):
        src_datapath = self.datapaths[dpid]
        # Get destination by load prediction
        dest_loc_dpid = self.get_desc_loc(dpid)
        if dest_loc_dpid != 0:
            print "Calculate destination finished, dpid: %d" % dest_loc_dpid
            dst_datapath = self.datapaths[dest_loc_dpid]
        else:
            print ("Calculate destination finished, Not enough history data, Migrate to DP3!!!")
            dst_datapath = self.datapaths[3]
        host_key = self.get_host_info(dpid, port)
        if host_key:
            host_vni = host_key[2]
            host_ip = self.access_table[host_key][0]
            host_mac = self.access_table[host_key][1]
            # because simulation of migration, must assign dst_port directly
            dst_port = 3
            if host_vni == 100:
                dst_port = 3
            elif host_vni == 200:
                dst_port = 4
            print "Start VM Migration: host(%s, %s, %d), src(%s, %s) -> dst(%s, %s)" % \
                  (host_ip, host_mac, host_vni, dpid, port, dst_datapath.id, dst_port)
            self.get_topology(event.EventPortModify)
            self.update_access_table(src_datapath, port, dst_datapath, dst_port, host_ip, host_mac, host_vni)
            print "Topology update finished, start flow entries modifying."
            self.flow_cacl.migration_calc(src_datapath, port, dst_datapath, dst_port,
                                          host_ip, host_mac, host_vni, self.vni_switch_list, self.datapaths)

    # self.access_table[(dpid, in_port, vni)] = (ip, mac)
    def update_access_table(self, src_dp, src_port, dst_dp, dst_port, host_ip, host_mac, vni):
        for key in self.access_table.keys():
            if key[0] == src_dp.id and key[1] == src_port:
                print "Delete this host in access table:" + str(self.access_table[key])
                del self.access_table[key]
        self.access_table[(dst_dp.id, dst_port, vni)] = (host_ip, host_mac)
        # send websocket message to tell web to update topology
        self._ws_broadcast("updateTopology", "None")
        # send migrate log to web
        migrate_strg = {"src_dp": src_dp.id, "src_port": src_port, "dst_dp": dst_dp.id, "dst_port": dst_port}
        self._ws_broadcast("migrateLog", migrate_strg)

    def _ws_broadcast(self, id, msg):
        disconnected_clients = []
        for rpc_client in self.rpc_clients:
            try:
                msg_send = {"id": id, "data": msg}
                rpc_client.ws.send(unicode(ujson.dumps(msg_send)))
            except SocketError:
                self.logger.debug('WebSocket disconnected: %s', rpc_client.ws)
                disconnected_clients.append(rpc_client)
            except InvalidReplyError as e:
                self.logger.error(e)

        for client in disconnected_clients:
            self.rpc_clients.remove(client)

    def _request_stats(self, datapath):
        parser = datapath.ofproto_parser
        dpid = datapath.id
        try:
            port_list = self.access_ports[dpid]
        except KeyError:
            print ("Error: Can't get DP%d's access_ports when request PortStats!" % dpid)
            return

        # Ask for all access ports info: port_no, packets statistics {1:(1,2,3)}
        # print "Request Switch%d Stats, and access_port: %s" % (dpid, str(self.access_ports))
        for port_no in port_list:
            req = parser.OFPPortStatsRequest(datapath, dpid, port_no)
            datapath.send_msg(req)

        # Ask for desc for all ports: name, curr_speed, mac_addr, max_speed, state
        req = parser.OFPPortDescStatsRequest(datapath, 0)
        datapath.send_msg(req)

    # key = (dpid, port_no)  value = speed
    def _save_stats(self, dist, key, value, length):
        if key not in dist:
            dist[key] = []
        dist[key].append(value)

        if len(dist[key]) > length:
            dist[key].pop(0)

    def _get_speed(self, now, pre, period):
        if period:
            return round((now - pre) / (period))
        else:
            return 0

    def _get_time(self, sec, nsec):
        return sec + nsec / (10 ** 9)

    def _get_period(self, n_sec, n_nsec, p_sec, p_nsec):
        return self._get_time(n_sec, n_nsec) - self._get_time(p_sec, p_nsec)

    def get_host_info(self, dpid, port):
        for key in self.access_table.keys():
            if key[0] == dpid and key[1] == port:
                return key
        # self.logger.debug("dpid:%s, port:%s, this host is not found." % dpid, port)
        return None

    # Get least loaded switch
    def get_desc_loc(self, src_dp):
        for dpid in self.switches:
            self.forecast.bp_predict(dpid, self.history_speed[dpid], self.switch_speed[dpid])
        print ("Calc desc location, prediction: %s" % str(self.forecast.predict_result))
        return self.forecast.get_result(src_dp)

    def register_access_info(self, dpid, in_port, ip, vni, mac):
        if in_port in self.access_ports[dpid]:
            if (dpid, in_port, vni) in self.access_table:
                if (ip, mac) != self.access_table[(dpid, in_port, vni)]:
                    self.access_table[(dpid, in_port, vni)] = (ip, mac)
            else:
                self.access_table[(dpid, in_port, vni)] = (ip, mac)

    def get_host_mac(self, ip):
        for host in self.access_table.values():
            if host[0] == ip:
                return host[1]
        return False

    def update_vxlan_group(self, dpid, vni):
        # { vni: [1,2,3]}
        self.vni_switch_list.setdefault(vni, [])
        if dpid not in self.vni_switch_list[vni]:
            self.vni_switch_list[vni].append(dpid)
            print "Add new vtep, DP%d -> VNI %d" % (dpid, vni)

    # get Adjacency matrix from link_to_port
    def get_graph(self, link_list):
        for src in self.switches:
            for dst in self.switches:
                self.graph.setdefault(src, {dst: float('inf')})
                if src == dst:
                    self.graph[src][src] = 0
                elif (src, dst) in link_list:
                    self.graph[src][dst] = 1
                else:
                    self.graph[src][dst] = float('inf')
        return self.graph

    # switch_port_table = {dp1:(1,2,3), dp2:(1,2,11), dp3:(1,2,11,13)}
    def create_port_map(self, switch_list):
        for sw in switch_list:
            dpid = sw.dp.id
            # dpid = sw[0]
            self.switch_port_table.setdefault(dpid, set())
            self.switch_port_down.setdefault(dpid, set())
            self.tunnel_ports.setdefault(dpid, set())
            self.access_ports.setdefault(dpid, set())
            self.switch_port_down.setdefault(dpid, set())

            for p in sw.ports:
                if p.is_live():
                    self.switch_port_table[dpid].add(p.port_no)
                else:
                    self.switch_port_down[dpid].add(p.port_no)
        print "Update port_table:%s" % str(self.switch_port_table)
        print "Update port_table_down:%s" % str(self.switch_port_down)

    # get links`srouce port to dst port from link_list,
    # link_to_port:(src_dpid,dst_dpid)->(src_port,dst_port)
    def create_tunnel_links(self, link_list):
        for link in link_list:
            src = link.src
            dst = link.dst
            self.link_to_port[(src.dpid, dst.dpid)] = (src.port_no, dst.port_no)

        for dp, port_list in self.switch_port_table.iteritems():
            for port_no in port_list:
                if port_no > 10:
                    self.tunnel_ports[dp].add(port_no)
        print "Update tunnel_port_list:%s" % str(self.tunnel_ports)

    # get ports without link into access_ports
    def create_access_ports(self):
        for sw in self.switch_port_table:
            self.access_ports[sw] = self.switch_port_table[sw] - self.tunnel_ports[sw]
        print "Update access_port_list:%s" % str(self.access_ports)

    # show topology in console
    def show_topology(self):
        switch_num = len(self.switches)
        # show link between switches
        if self.pre_link_to_port != self.link_to_port or IS_UPDATE:
            print("----------------------Link Port-----------------------")
            print '%10s' % ("switch"),
            for i in xrange(1, switch_num + 1):
                print '%10d' % i,
            print ""
            for i in xrange(1, switch_num + 1):
                print '%10d' % i,
                for j in xrange(1, switch_num + 1):
                    if (i, j) in self.link_to_port.keys():
                        print '%10s' % str(self.link_to_port[(i, j)]),
                    else:
                        print '%10s' % "No-link",
                print ""
            self.pre_link_to_port = copy.deepcopy(self.link_to_port)
            print "\n"

        # each dp access host
        # {(dpid, inport, vni): (ip, mac)}
        if self.pre_access_table != self.access_table or IS_UPDATE:
            print "---------------------Access Host-----------------------"
            print "%10s %10s %10s     %20s" % ("switch", "port", "vni", "Host")
            if not self.access_table.keys():
                print "    NO found host"
            else:
                for tup in self.access_table:
                    try:
                        print '%10d %10d %10d     %20s' % (tup[0], tup[1], tup[2], self.access_table[tup])
                    except TypeError:
                        print "Some Error appear, this is access host table: "
                        print str(self.access_table)
            self.pre_access_table = copy.deepcopy(self.access_table)
        print "\n"


# Define REST API
class TopoMonitorController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(TopoMonitorController, self).__init__(req, link, data, **config)
        path = "%s/web/" % PATH
        self.static_app = DirectoryApp(path)
        self.dc_network = data['dc_network']
        self.nodexy = {
            "switch": [{"x": 200, "y": 100}, {"x": 350, "y": 250}, {"x": 500, "y": 100}],
            "host": {}
        }
        self.host_name = {"10.0.10.1": "RED-1", "10.0.10.2": "RED-2", "10.0.10.3": "RED-3",
                          "10.0.20.1": "BLUE-1", "10.0.20.2": "BLUE-2", "10.0.20.3": "BLUE-3"}

    # Calculate device coord in topology graph
    def get_coord(self, dpid, host_num):
        if dpid == 1:
            if host_num == 1:
                self.nodexy["host"][dpid] = [{"x": 100, "y": 100}]
            elif host_num == 2:
                self.nodexy["host"][dpid] = [{"x": 100, "y": 25}, {"x": 100, "y": 175}]
            elif host_num == 3:
                self.nodexy["host"][dpid] = [{"x": 100, "y": 25}, {"x": 100, "y": 100}, {"x": 100, "y": 175}]
            elif host_num == 4:
                self.nodexy["host"][dpid] = [{"x": 100, "y": 25}, {"x": 100, "y": 75}, {"x": 100, "y": 125},
                                             {"x": 100, "y": 175}]
        elif dpid == 2:
            if host_num == 1:
                self.nodexy["host"][dpid] = [{"x": 350, "y": 350}]
            elif host_num == 2:
                self.nodexy["host"][dpid] = [{"x": 275, "y": 350}, {"x": 425, "y": 350}]
            elif host_num == 3:
                self.nodexy["host"][dpid] = [{"x": 275, "y": 350}, {"x": 350, "y": 350}, {"x": 425, "y": 350}]
            elif host_num == 4:
                self.nodexy["host"][dpid] = [{"x": 275, "y": 350}, {"x": 325, "y": 350}, {"x": 375, "y": 350},
                                             {"x": 425, "y": 350}]
        elif dpid == 3:
            if host_num == 1:
                self.nodexy["host"][dpid] = [{"x": 600, "y": 100}]
            elif host_num == 2:
                self.nodexy["host"][dpid] = [{"x": 600, "y": 25}, {"x": 600, "y": 175}]
            elif host_num == 3:
                self.nodexy["host"][dpid] = [{"x": 600, "y": 25}, {"x": 600, "y": 100}, {"x": 600, "y": 175}]
            elif host_num == 4:
                self.nodexy["host"][dpid] = [{"x": 600, "y": 25}, {"x": 600, "y": 75}, {"x": 600, "y": 125},
                                             {"x": 600, "y": 175}]

    # GET latest port statistic information
    @route('data', '/speed/realspeed',
           methods=['GET'], requirements={'dpid': dpid_lib.DPID_PATTERN})
    def get_real_speed(self, req, **kwargs):
        switch_status_list = self.dc_network.port_speed
        body = ujson.dumps(switch_status_list, indent=4)
        return Response(content_type='application/json', body=body)

    # GET load prediction result
    @route('data', '/speed/result', methods=['GET'])
    def get_history_speed(self, req, **kwargs):
        for dpid in self.dc_network.switches:
            self.dc_network.forecast.bp_predict(dpid, self.dc_network.history_speed[dpid],
                                                self.dc_network.switch_speed[dpid])
        predict_result = self.dc_network.forecast.predict_result
        body = ujson.dumps(predict_result, indent=4)
        return Response(content_type='application/json', body=body)

    # WEBSOCKET push some real-time message to web
    @websocket('ws', '/ws/broadcast')
    def add_ws_client(self, ws):
        rpc_client = WebSocketRPCClient(ws)
        self.dc_network.rpc_clients.append(rpc_client)
        rpc_client.serve_forever()

    # GET latest topology graph data for NEXT-UI
    @route('topology', '/topology/graph', methods=['GET'])
    def get_hosts(self, req, **kwargs):
        topology_data = dict()
        topology_data["nodes"] = []
        topology_data["links"] = []
        node = dict()
        link = dict()
        for dpid in self.dc_network.switches:
            try:
                host_num = len(self.dc_network.access_ports[dpid])
            except KeyError:
                continue
            # set host coord  in nodexy
            self.get_coord(dpid, host_num)
            node["id"] = dpid
            node["x"] = self.nodexy["switch"][dpid - 1]["x"]
            node["y"] = self.nodexy["switch"][dpid - 1]["y"]
            node["vni"] = "100, 200"
            node["name"] = "OVS-" + str(dpid)
            node["ip"] = "192.168." + str(dpid) + "0.2"
            node["device_type"] = "switch"
            topology_data["nodes"].append(copy.deepcopy(node))

        for tunnel_dpid in self.dc_network.link_to_port.keys():
            link["name"] = "VXLAN隧道"
            link["source"] = tunnel_dpid[0]
            link["target"] = tunnel_dpid[1]
            link["src_port"] = tunnel_dpid[1]+10
            link["dst_port"] = tunnel_dpid[0]+10
            topology_data["links"].append(copy.deepcopy(link))

        nodexy = copy.deepcopy(self.nodexy)
        for host_key, host_info in self.dc_network.access_table.iteritems():
            dpid = host_key[0]
            port_no = host_key[1]
            vni = host_key[2]
            ip = host_info[0]
            mac = host_info[1]
            print("get host locxy host_key:" + str(host_key))
            locxy = nodexy["host"][int(dpid)].pop()
            node["id"] = dpid * 10 + port_no
            node["x"] = locxy["x"]
            node["y"] = locxy["y"]
            node["vni"] = vni
            node["name"] = self.host_name[ip]
            node["ip"] = ip
            node["mac"] = mac
            node["device_type"] = "host"
            topology_data["nodes"].append(copy.deepcopy(node))
            link["name"] = "AC链路"
            link["source"] = dpid
            link["target"] = dpid * 10 + port_no
            link["src_port"] = port_no
            link["dst_port"] = 1
            topology_data["links"].append(copy.deepcopy(link))

        # body = json.dumps(topology_data, cls=SetEncoder)
        body = ujson.dumps(topology_data, indent=4)
        return Response(content_type='application/json', body=body)

    # POST VM migration command
    @route('topology', '/topology/migrate', methods=['POST'])
    def vm_migrate(self, req, **kwargs):
        # dpid = dpid_lib.str_to_dpid(kwargs['dpid'])
        # if dpid not in self.web_app.mac_to_port:
        #     return Response(status=
        migrate_params = ujson.loads(req.body) if req.body else {}
        if migrate_params["src_dp"] == migrate_params["dst_dp"]:
            response = {"status": "fail", "reason": "迁移策略不合法!"}
            body = ujson.dumps(response, indent=4)
            return Response(content_type='application/json', body=body)
        src_dp = int(migrate_params["src_dp"])
        src_port = int(migrate_params["src_port"])
        dst_dp = int(migrate_params["dst_dp"])
        dst_port = int(migrate_params["dst_port"])
        host_key = self.dc_network.get_host_info(src_dp, src_port)
        if host_key:
            host_vni = host_key[2]
            host_ip = self.dc_network.access_table[host_key][0]
            host_mac = self.dc_network.access_table[host_key][1]
            src_datapath = self.dc_network.datapaths[src_dp]
            dst_datapath = self.dc_network.datapaths[dst_dp]
            print "Start VM Migration: host(%s, %s, %d), src(%s, %s) -> dst(%s, %s)" % \
                  (host_ip, host_mac, host_vni, src_dp, src_port, dst_dp, dst_port)
            self.dc_network.get_topology(None)
            self.dc_network.update_access_table(src_datapath, src_port,
                                                dst_datapath, dst_port,
                                                host_ip, host_mac, host_vni)
            print "topology update finished, start flow calc"
            self.dc_network.flow_cacl.migration_calc(src_datapath, src_port,
                                                     dst_datapath, dst_port,
                                                     host_ip, host_mac, host_vni,
                                                     self.dc_network.vni_switch_list,
                                                     self.dc_network.datapaths)
            response = {"status": "ok"}
        else:
            response = {"status": "fail", "reason": "源主机不存在!"}

        body = ujson.dumps(response, indent=4)
        return Response(content_type='application/json', body=body)

    # PUT create a VM on specified location
    @route('topology', '/topology/vm', methods=['PUT'])
    def vm_create(self, req, **kwargs):
        vm_params = ujson.loads(req.body) if req.body else {}
        if vm_params["vni"] is None:
            response = {"status": "fail", "reason": "未指定创建虚拟机所属VNI!"}
            body = ujson.dumps(response, indent=4)
            return Response(content_type='application/json', body=body)

        dpid = int(vm_params["dpid"])
        port = int(vm_params["port"])
        vni = int(vm_params["vni"])
        datapath = self.dc_network.datapaths[dpid]
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(in_port=port)
        actions = [parser.OFPActionSetField(tunnel_id=vni)]
        self.dc_network.flow_cacl.add_flow_table(datapath, 10, match, actions, goto_table_id=1, table_id=0)

        response = {"status": "ok"}
        body = ujson.dumps(response, indent=4)
        return Response(content_type='application/json', body=body)

    # DELETE delete a VM on specified location
    @route('topology', '/topology/vm', methods=['DELETE'])
    def vm_delete(self, req, **kwargs):
        vm_params = ujson.loads(req.body) if req.body else {}
        if vm_params["vni"] is None:
            response = {"status": "fail", "reason": "未指定删除虚拟机所属VNI!"}
            body = ujson.dumps(response, indent=4)
            return Response(content_type='application/json', body=body)
        dpid = int(vm_params["dpid"])
        port = int(vm_params["port"])
        vni = int(vm_params["vni"])
        host_key = self.dc_network.get_host_info(dpid, port)
        if host_key and host_key[2] == vni:
            host_ip = self.dc_network.access_table[host_key][0]
            host_mac = self.dc_network.access_table[host_key][1]
            datapaths = self.dc_network.datapaths
            datapath = datapaths[dpid]
            parser = datapath.ofproto_parser
            match = parser.OFPMatch(in_port=port)
            self.dc_network.flow_cacl.del_flow_table(datapath, match, 0, 10)
            for vni_dpid in self.dc_network.vni_switch_list[vni]:
                if vni_dpid != dpid:
                    match = parser.OFPMatch(tunnel_id=vni, eth_dst=host_mac)
                    self.dc_network.flow_cacl.del_flow_table(datapaths[vni_dpid], match, 2, 100)
                    match = parser.OFPMatch(eth_type=0x0806, tunnel_id=vni, arp_tpa=host_ip)
                    self.dc_network.flow_cacl.del_flow_table(datapaths[vni_dpid], match, 2, 100)
            response = {"status": "ok"}
        else:
            response = {"status": "fail", "reason": "指定的虚拟机不存在!"}

        body = ujson.dumps(response, indent=4)
        return Response(content_type='application/json', body=body)

    # STATIC Web File Route    http://127.0.0.1:8080/web/index.html
    @route('static', '/web/{filename:.*}')
    def static_handler(self, req, **kwargs):
        if kwargs['filename']:
            req.path_info = kwargs['filename']
        return self.static_app(req)
