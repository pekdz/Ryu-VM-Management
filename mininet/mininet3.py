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
    h1 = net.addHost('red2', mac='00:00:00:00:10:02', ip='10.0.10.2/24')
    h2 = net.addHost('blue3', mac='00:00:00:00:20:03', ip='10.0.20.3/24')
    h3 = net.addHost('red1', mac='00:00:00:00:10:01', ip='10.0.10.1/24')
    h4 = net.addHost('blue1', mac='00:00:00:00:20:01', ip='10.0.20.1/24')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    ctrl = RemoteController('ryu', ip='192.168.30.1')

    print "*** Creating links"
    net.addLink(s3, h1, bw=10)
    net.addLink(s3, h2, bw=10)
    net.addLink(s3, h3, bw=10)
    net.addLink(s3, h4, bw=10)

    print "*** Starting network"
    net.start()
    ctrl.start()
    s3.start([ctrl])
    s3.cmd(
        'ovs-vsctl add-port s3 vtep1 -- set interface vtep1 type=vxlan option:remote_ip=192.168.10.2 option:key=flow ofport_request=11 &')
    s3.cmd(
        'ovs-vsctl add-port s3 vtep2 -- set interface vtep2 type=vxlan option:remote_ip=192.168.20.2 option:key=flow ofport_request=12 &')
    print "***Turn down shadow ports"
    s3.cmd('ifconfig s3-eth3 down &')
    s3.cmd('ifconfig s3-eth4 down &')
    print "*** Running Ping"
    time.sleep(2)
    h1.cmd('ping -c 1 10.0.10.10 &')
    h1.cmd('ifconfig red2-eth0 mtu 1450')
    h2.cmd('ping -c 1 10.0.20.10 &')
    h2.cmd('ifconfig blue3-eth0 mtu 1450')
    h3.cmd('ifconfig red1-eth0 mtu 1450')
    # h3.cmd('xterm &')
    # h3.cmd('python -m SimpleHTTPServer 80 &')
    h4.cmd('ifconfig blue1-eth0 mtu 1450')
    print "*** Running CLI"
    CLI(net)
    print "*** Stopping network"
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    topology()
