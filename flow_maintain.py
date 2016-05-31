from __future__ import division
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
import crm_database

SLEEP_PERIOD = 30


class FlowTableMaintain(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _NAME = 'FlowMaintain'

    def __init__(self, *args, **kwargs):
        super(FlowTableMaintain, self).__init__(*args, **kwargs)
        self.switch_vxlan_port = crm_database.SWITCH_VXLAN_PORT
        self.topology_api_app = self

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # install table-miss flow entry
        match = parser.OFPMatch()
        actions = []
        self.add_flow_table(datapath, 1, match, actions, goto_table_id=1, table_id=0)
        self.add_flow_table(datapath, 1, match, actions, goto_table_id=2, table_id=1)
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow_table(datapath, 1, match, actions, table_id=2)

        # install initial vni flow entry
        for dpid, host_list in crm_database.SWITCH_INFO.iteritems():
            if datapath.id == dpid:
                for host in host_list:
                    # dpid : (vni, port, ip, mac)
                    match = parser.OFPMatch(in_port=host[1])
                    actions = [parser.OFPActionSetField(tunnel_id=host[0])]
                    self.add_flow_table(datapath, 10, match, actions, goto_table_id=1, table_id=0)

    def add_flow_table(self, datapath, priority, match, actions, goto_table_id=0, table_id=0, hard_timeout=0,):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        if goto_table_id == 0:
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                                 actions)]
        else:
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                                 actions),
                    parser.OFPInstructionGotoTable(table_id=goto_table_id)]

        mod = parser.OFPFlowMod(datapath=datapath, table_id=table_id, priority=priority, hard_timeout=hard_timeout,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    def del_flow_table(self, datapath, match,  table_id, priority):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        mod = parser.OFPFlowMod(datapath=datapath,
                                command=ofproto.OFPFC_DELETE_STRICT,
                                priority=priority,
                                out_port=ofproto.OFPP_ANY,
                                out_group=ofproto.OFPG_ANY,
                                match=match, table_id=table_id)

        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPErrorMsg, MAIN_DISPATCHER)
    def error_msg_handler(self, ev):
        msg = ev.msg
        print "OFPErrorMsg received: type=0x%02x code=0x%02x, message=%s"\
              % (msg.type, msg.code, self.hex_array(msg.data))

    def hex_array(self, data):
        # convert data into bytearray explicitly
        return ' '.join('0x%02x' % byte for byte in bytearray(data))

    # regular update flow entry
    # table1: transfer packets to access_host
    # table2: transfer packets to specific tunnel to remote host
    def switch_calc(self, datapaths, host_list, vni_switch_list, tunnel_ports):
        for datapath in datapaths.values():
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            dpid = datapath.id
            access_host = [host for host in host_list if host[0] == dpid]
            print "Switch_Calc, access_host:" + str(access_host)

            for host_key in access_host:
                host_ip = host_list[host_key][0]
                host_mac = host_list[host_key][1]
                host_vni = host_key[2]
                host_port = host_key[1]

                # local vm flow entry
                match = parser.OFPMatch(tunnel_id=host_vni, eth_dst=host_mac)
                actions = [parser.OFPActionOutput(host_port)]
                self.add_flow_table(datapath, 10, match, actions, table_id=1)

                # remote vm flow entry
                for vni_dpid in vni_switch_list[host_vni]:
                    if vni_dpid != dpid:
                        # normal packets
                        match = parser.OFPMatch(tunnel_id=host_vni, eth_dst=host_mac)
                        actions = [parser.OFPActionOutput(dpid+10)]
                        self.add_flow_table(datapaths[int(vni_dpid)], 100, match, actions, table_id=2)

    # vni_switch_list ={ vni:[1,2,3]}    switch_vxlan_port={1:[(2,12),(3,13)],...}
    def get_vxlan_port(self, dpid, vni, vni_switch_list):
        vtep_in_vni = vni_switch_list[vni]
        vxlan_port_list = set()
        for switch_in_vni in vtep_in_vni:
            if switch_in_vni != dpid:
                #(dpid,vxlan_port)
                for port in self.switch_vxlan_port[dpid]:
                    if port[0] == switch_in_vni:
                        vxlan_port_list.add(port[1])
        print "GET VXLAN PORT:" + str(vxlan_port_list)
        return vxlan_port_list

    def migration_calc(self, src_dp, src_port, dst_dp, dst_port, host_ip, host_mac, vni, vni_switch_list, datapaths):
        # Source switch
        datapath = src_dp
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Source switch
        # register flow-entry
        match = parser.OFPMatch(in_port=src_port)
        self.del_flow_table(datapath, match, 0, 10)
        # local transmit flow-entry in table 1
        match = parser.OFPMatch(tunnel_id=vni, eth_dst=host_mac)
        self.del_flow_table(datapath, match, 1, 10)
        match = parser.OFPMatch(in_port=src_port, tunnel_id=vni)
        self.del_flow_table(datapath, match, 2, 10)

        # Destination switch
        datapath = dst_dp
        parser = datapath.ofproto_parser

        match = parser.OFPMatch(in_port=dst_port)
        actions = [parser.OFPActionSetField(tunnel_id=vni)]
        self.add_flow_table(datapath, 10, match, actions, goto_table_id=1, table_id=0)

        match = parser.OFPMatch(tunnel_id=vni, eth_dst=host_mac)
        actions = [parser.OFPActionOutput(dst_port)]
        self.add_flow_table(datapath, 10, match, actions, table_id=1)

        # other switch
        # precise match to remote vm
        for vni_dpid in vni_switch_list[vni]:
            if vni_dpid != src_dp.id:
                match = parser.OFPMatch(tunnel_id=vni, eth_dst=host_mac)
                self.del_flow_table(datapaths[vni_dpid], match, 2, 100)

        for vni_dpid in vni_switch_list[vni]:
            if vni_dpid != dst_dp.id:
                match = parser.OFPMatch(tunnel_id=vni, eth_dst=host_mac)
                actions = [parser.OFPActionOutput(dst_dp.id + 10)]
                self.add_flow_table(datapaths[vni_dpid], 100, match, actions, table_id=2)


