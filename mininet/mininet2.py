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
    h1 = net.addHost('red3', mac='00:00:00:00:10:03', ip='10.0.10.3/24')
    h2 = net.addHost('blue2', mac='00:00:00:00:20:02', ip='10.0.20.2/24')
    h3 = net.addHost('red1', mac='00:00:00:00:10:01', ip='10.0.10.1/24')
    h4 = net.addHost('blue1', mac='00:00:00:00:20:01', ip='10.0.20.1/24')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    ctrl = RemoteController('ryu', ip='192.168.20.1')

    print "*** Creating links"
    net.addLink(s2, h1, bw=10)
    net.addLink(s2, h2, bw=10)
    net.addLink(s2, h3, bw=10)
    net.addLink(s2, h4, bw=10)

    print "*** Starting network"
    net.build()
    ctrl.start()
    s2.start([ctrl])
    print "**Create VXLAN Tunnel: vtep1 vtep3"
    s2.cmd(
        'ovs-vsctl add-port s2 vtep1 -- set interface vtep1 type=vxlan option:remote_ip=192.168.10.2 option:key=flow ofport_request=11 &')
    s2.cmd(
        'ovs-vsctl add-port s2 vtep3 -- set interface vtep3 type=vxlan option:remote_ip=192.168.30.2 option:key=flow ofport_request=13 &')
    print "***Turn down shadow ports"
    s2.cmd('ifconfig s2-eth3 down &')
    s2.cmd('ifconfig s2-eth4 down &')
    print "*** Running PingAll"
    time.sleep(4)
    h1.cmd('ping -c 1 10.0.10.10 &')
    h1.cmd('ifconfig red3-eth0 mtu 1450')
    h2.cmd('ping -c 1 10.0.20.10 &')
    h2.cmd('ifconfig blue2-eth0 mtu 1450')
    h3.cmd('ifconfig red1-eth0 mtu 1450')
    # h3.cmd('python -m SimpleHTTPServer 80 &')
    h4.cmd('ifconfig blue1-eth0 mtu 1450')
    print "*** Running CLI"
    CLI(net)
    print "*** Stopping network"
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    topology()
