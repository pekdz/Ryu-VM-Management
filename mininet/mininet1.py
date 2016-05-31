from mininet.net import Mininet
from mininet.node import Controller, RemoteController, OVSKernelSwitch, IVSSwitch, UserSwitch
from mininet.link import Link, TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.link import Intf
import time


def topology():
    net = Mininet(controller=Controller, link=TCLink, switch=OVSKernelSwitch)

    print "*** Creating nodes"
    h1 = net.addHost('red1', mac='00:00:00:00:10:01', ip='10.0.10.1/24')
    h2 = net.addHost('blue1', mac='00:00:00:00:20:01', ip='10.0.20.1/24')

    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    ctrl = RemoteController('ryu', ip='192.168.10.1')

    print "*** Creating links"
    net.addLink(s1, h1, bw=10)
    net.addLink(s1, h2, bw=10)

    print "*** Starting network"
    net.build()
    ctrl.start()
    s1.start([ctrl])
    s1.cmd(
        'ovs-vsctl add-port s1 vtep2 -- set interface vtep2 type=vxlan option:remote_ip=192.168.20.2 option:key=flow ofport_request=12 &')
    s1.cmd(
        'ovs-vsctl add-port s1 vtep3 -- set interface vtep3 type=vxlan option:remote_ip=192.168.30.2 option:key=flow ofport_request=13 &')
    print "*** Running Ping"
    time.sleep(5)
    h1.cmd('ping -c 1 10.0.10.10 &')
    h1.cmd('ifconfig red1-eth0 mtu 1450')
    # h1.cmd('python -m SimpleHTTPServer 80 &')
    h2.cmd('ping -c 1 10.0.20.10 &')
    h2.cmd('ifconfig blue1-eth0 mtu 1450')

    print "*** Running CLI"
    CLI(net)
    print "*** Stopping network"
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    topology()
