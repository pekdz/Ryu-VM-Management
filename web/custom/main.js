"use strict";
$(document).ready(function() {
    var getTopoTimeout;
    
    /* Connect to Ryu Websocket API */
    var ws = new WebSocket("ws://" + location.host + "/ws/broadcast");
    ws.onopen = function(){ 
        console.log("与后端建立Webocket连接!")
    };
    ws.onmessage = function(event) {
        var msg = JSON.parse(event.data);
        console.log(msg.id);
        if(msg.id == "updateTopology"){
            try{
                clearTimeout(getTopoTimeout);
            }catch(e){
                return;
            }
            getTopoTimeout = setTimeout(function(){
                getTopology();
            }, 1500)
        }
        else if(msg.id == "updateSpeed")
            updateSpeedTable(msg.data);
        else if(msg.id == "migrateLog")
            updateLog(msg.data);
        else if(msg.id == "errorLog")
            showErrorLog(msg.data);
    };

    /* Next UI Topology initialize */
    var topologyData ={
        nodes: [{
            "id": 1,
            "x": 200,
            "y": 100,
            "vni": "100,200",
            "name": "OVS-1",
            "ip": "192.168.10.2",
            "device_type": "switch"
        }, {
            "id": 2,
            "x": 350,
            "y": 250,
            "name": "OVS-2",
            "vni": "100,200",
            "ip": "192.168.20.2",
            "device_type": "switch"
        }, {
            "id": 3,
            "x": 500,
            "y": 100,
            "name": "OVS-3",
            "vni": "100,200",
            "ip": "192.168.30.2",
            "device_type": "switch"
        }, {
            "id": 11,
            "x": 100,
            "y": 25,
            "name": "RED-1",
            "vni": "100",
            "ip": "10.0.10.1",
            "device_type": "host"
        }, {
            "id": 12,
            "x": 100,
            "y": 175,
            "name": "BLUE-1",
            "vni": 200,
            "ip": "10.0.20.1",
            "device_type": "host"
        }, {
            "id": 21,
            "x": 275,
            "y": 350,
            "name": "RED-3",
            "vni": "100",
            "ip": "10.0.10.3",
            "device_type": "host"
        }, {
            "id": 22,
            "x": 425,
            "y": 350,
            "name": "BLUE-2",
            "vni": "200",
            "ip": "10.0.20.2",
            "device_type": "host"
        }, {
            "id": 31,
            "x": 600,
            "y": 25,
            "name": "RED-2",
            "vni": "100",
            "ip": "10.0.10.2",
            "device_type": "host"
        }, {
            "id": 32,
            "x": 600,
            "y": 175,
            "name": "BLUE-3",
            "vni": "200",
            "ip": "10.0.20.3",
            "device_type": "host"
        }],
        links: [{
            "name": "VXLAN Tunnel",
            "source": 1,
            "target": 2,
            "src_port": 12,
            "dst_port": 11
        },{
            "source": 2,
            "target": 1,
            "src_port": 11,
            "dst_port": 12
        }, {
            "source": 2,
            "target": 3,
            "src_port": 13,
            "dst_port": 12
        }, {
            "source": 1,
            "target": 3,
            "src_port": 13,
            "dst_port": 11
        }, {
            "source": 1,
            "target": 11,
            "src_port": 1,
            "dst_port": 1
        }, {
            "source": 1,
            "target": 12,
            "src_port": 2,
            "dst_port": 1
        }, {
            "source": 2,
            "target": 21,
            "src_port": 1,
            "dst_port": 1
        }, {
            "source": 2,
            "target": 22,
            "src_port": 2,
            "dst_port": 1
        }, {
            "source": 3,
            "target": 31,
            "src_port": 1,
            "dst_port": 1
        }, {
            "source": 3,
            "target": 32,
            "src_port": 2,
            "dst_port": 1
        }]
    };
    (function(nx, global) {
        nx.define('MyNodeTooltip', nx.ui.Component, {
            properties: {
                node: {},
                topology: {}
            },
            view: {
                props: {
                    'class': "topology-tooltip"
                },
                content: [
                    {
                        tag: 'h5',
                        content: '{#node.model.name}'
                    },
                    {
                        tag: 'p',
                        content: [{
                            tag: 'label',
                            content: 'VNI: '
                        },
                        {
                            tag: 'span',
                            content: '{#node.model.vni}'
                        }]
                    },
                    {
                        tag: 'p',
                        content: [{
                            tag: 'label',
                            content: 'IP: '
                        },
                        {
                            tag: 'span',
                            content: '{#node.model.ip}'
                        }]
                    }
                ]
            }
        });
        nx.define('MyLinkTooltip', nx.ui.Component, {
            properties: {
                link: {},
                topology: {}
            },
            view: {
                content: [{
                    tag: 'p',
                    content: [{
                        tag: 'h5',
                        content: '{#link.model.name}'
                    },{
                        tag: 'p',
                        content: [{
                            tag: 'label',
                            content: '源端口: '
                        }, {
                            tag: 'span',
                            content: '{#link.model.src_port}'
                        }]
                    },{
                        tag: 'p',
                        content: [{
                            tag: 'label',
                            content: '目的端口: '
                        }, {
                            tag: 'span',
                            content: '{#link.model.dst_port}'
                        }]
                    }]
                }]
            }
        });
        nx.define('TopologyConfig', nx.ui.Component, {
            properties: {
            },
            view: {
                props: {
                    'class': "topology-next"
                },
                content: {
                    name: 'topo',
                    type: 'nx.graphic.Topology',
                    props: {
                        style: 'border:1px solid #ccc;',
                        adaptive: true,
                        nodeConfig: {
                            label: 'model.name',
                            iconType: 'model.device_type'
                        },
                        nodeSetConfig: {
                            iconType: 'model.device_type'
                        },
                        tooltipManagerConfig: {
                            nodeTooltipContentClass: 'MyNodeTooltip',
                            linkTooltipContentClass: 'MyLinkTooltip'
                        },
                        showIcon: true,
                        identityKey: 'id',
                        data: topologyData
                    }
                }
            }
        });

        //Start Function
        var Shell = nx.define(nx.ui.Application, {
            methods: {
                start: function () {
                    var view = new TopologyConfig();
                    view.attach(this);
                }
            }
        });

        //Global val exposed to window
        global.shell = new Shell();
        global.shell.container(document.getElementById('topology-graph'));
        // global.shell.start();

    })(nx, nx.global);
    function getTopology(){
        $.ajax({
            headers : {
                'Accept' : 'application/json',
                'Content-Type' : 'application/json'
            },
            type: 'GET',
            url: '/topology/graph',
            success: function(response){
                topologyData = response;
                updateGraph();
            },
            error: function(jqXHR, textStatus, errorThrown){
                updateGraph();

            },
            dataType: 'json',
            timeout: 2000
        });
    }

    /* Update topology when got notification */
    function updateGraph(){
        $(".topology-next").remove();
        nx.define('TopologyConfig', nx.ui.Component, {
            properties: {
            },
            view: {
                props: {
                    'class': "topology-next"
                },
                content: {
                    name: 'topo',
                    type: 'nx.graphic.Topology',
                    props: {
                        style: 'border:1px solid #ccc;',
                        adaptive: true,
                        nodeConfig: {
                            label: 'model.name',
                            iconType: 'model.device_type'
                        },
                        nodeSetConfig: {
                            iconType: 'model.device_type'
                        },
                        tooltipManagerConfig: {
                            nodeTooltipContentClass: 'MyNodeTooltip',
                            linkTooltipContentClass: 'MyLinkTooltip'
                        },
                        showIcon: true,
                        identityKey: 'id',
                        data: topologyData
                    }
                }
            }
        });
        nx.global.shell.start();
    }
    $(window).resize(function(){updateGraph()});
    getTopology();
    // setInterval(function(){getTopology();}, 60000)
});

/* Update Real Statistic Information*/
function updateSpeedTable(speedData){
    var tempTable = '', speedTable = $("#speed-table-content");
    speedTable.empty();
    for(var i=1; i<4; i++){
        if(speedData[i]){
            $.each(speedData[i],function(port_no, data){
                tempTable += '<tr><td class="switch-name">'
                    +i+'</td><td>'
                    +port_no+'</td><td class="port-packet">'
                    +data[0]+'</td><td class="port-packet">'
                    +data[1]+'</td> <td class="port-speed">'
                    +data[2]+'</td></tr>';
            });
        }
    }
    speedTable.append(tempTable);
}

/* Send Migration Strategy to Controller */
function postMigrate() {
    $.confirm({
        title: '确认下发您的迁移策略?',
        confirmButtonClass: 'green white-text',
        cancelButtonClass: 'red lighten-1 white-text',
        confirmButton: '确认',
        cancelButton: '取消',
        content: false,
        columnClass: 'col push-s4 s4',
        theme: 'material',
        confirm: function(){
            var payload = JSON.stringify($("#migrate-form").serializeJson());
            $.ajax({
                headers : {
                    'Accept' : 'application/json',
                    'Content-Type' : 'application/json'
                },
                type: 'POST',
                data: payload,
                url: '/topology/migrate',
                success: function(response){
                    if(response.status == 'ok'){
                        toastr["info"]("迁移策略下发成功!");
                    }else{
                        toastr["error"]("下发失败, " + response.reason);
                    }
                },
                error: function(jqXHR, textStatus, errorThrown){
                    toastr["error"]("请求不成功!");
                },
                dataType: 'json',
                timeout: 2000
            });
        }
    });
}

/* Request Predicted Result from Controller */
function getPrediction() {
    var getBtn = $("#getPredictBtn");
    getBtn.html('获取中<i class="mdi-navigation-refresh right"></i>').attr('disabled','disabled');
    $.ajax({
        headers : {
            'Accept' : 'application/json',
            'Content-Type' : 'application/json'
        },
        type: 'GET',
        url: '/speed/result',
        success: function(response){
            getBtn.html('刷新预测<i class="mdi-navigation-refresh right"></i>').removeAttr('disabled');
            if(response[1] || response[2] || response[3]){
                var dp1Speed = response[1],
                    dp2Speed = response[2],
                    dp3Speed = response[3];
                if(dp1Speed)
                    $("#dp1-predict").text(Math.ceil((response[1][0]+response[1][1])/2));
                if(dp2Speed)
                    $("#dp2-predict").text(Math.ceil((response[2][0]+response[2][1])/2));
                if(dp3Speed)
                    $("#dp3-predict").text(Math.ceil((response[3][0]+response[3][1])/2));
            }else{
                toastr["error"]("尚未收集足够历史数据进行预测!");
            }

        },
        error: function(jqXHR, textStatus, errorThrown){
            toastr["error"]("请求不成功, 错误代码:" + jqXHR.status);
            getBtn.html('刷新预测<i class="mdi-navigation-refresh right"></i>').removeAttr('disabled');
        },
        dataType: 'json',
        timeout: 2000
    });
}

/* Update Migration Log */
function updateLog(log) {
    var timeNow = moment().format('YYYY-MM-DD HH:mm:ss'),
        tempLog = '<p><i class="mdi-social-notifications"></i> 虚拟机迁移: OVS-'
            +log.src_dp+' Port'
            +log.src_port+'  ->  OVS-'
            +log.dst_dp+' Port'
            +log.dst_port+'<span class="right"><i class="mdi-av-timer"></i> '
            +timeNow+'</span></p>';
    $("#migrate-log-list").append(tempLog);
}

function showErrorLog(log) {
    if(log.type == 1)
        toastr["error"]("警告: 交换机"+log.dpid+"掉线!");
    else if(log.type == 2)
        toastr["error"]("警告: 交换机"+log.dpid+"端口"+log.port+"挂起!");
}


/* Open VM Create and Delete Modal */
function createVM(){
    $('#vm-modal-title').text('创建虚拟机');
    $('#vm-confirm-btn').attr('name', 'create');
    $('#vmModal').openModal();
}
function deleteVM(){
    $('#vm-modal-title').text('删除虚拟机');
    $('#vm-confirm-btn').attr('name', 'delete');
    $('#vmModal').openModal();
}

/* Send VM Create and Delete Request to Controller */
function modifyVM(method){
    if(method == 'create'){
        $.confirm({
            title: '确认创建该虚拟机?',
            confirmButtonClass: 'green white-text',
            cancelButtonClass: 'red lighten-1 white-text',
            confirmButton: '确认',
            cancelButton: '取消',
            content: false,
            columnClass: 'col push-s4 s4',
            theme: 'material',
            confirm: function(){
                var payload = JSON.stringify($("#vm-form").serializeJson());
                $.ajax({
                    headers : {
                        'Accept' : 'application/json',
                        'Content-Type' : 'application/json'
                    },
                    type: 'PUT',
                    data: payload,
                    url: '/topology/vm',
                    success: function(response){
                        if(response.status == 'ok'){
                            toastr["info"]("虚拟机注册流表下发成功!");
                        }else{
                            toastr["error"]("虚拟机创建失败, " + response.reason);
                        }
                    },
                    error: function(jqXHR, textStatus, errorThrown){
                        toastr["error"]("请求不成功!");
                    },
                    dataType: 'json',
                    timeout: 2000
                });
            }
        });
    }else if(method == 'delete'){
        $.confirm({
            title: '确认删除该虚拟机?',
            confirmButtonClass: 'green white-text',
            cancelButtonClass: 'red lighten-1 white-text',
            confirmButton: '确认',
            cancelButton: '取消',
            content: false,
            columnClass: 'col push-s4 s4',
            theme: 'material',
            confirm: function(){
                var payload = JSON.stringify($("#vm-form").serializeJson());
                $.ajax({
                    headers : {
                        'Accept' : 'application/json',
                        'Content-Type' : 'application/json'
                    },
                    type: 'DELETE',
                    data: payload,
                    url: '/topology/vm',
                    success: function(response){
                        if(response.status == 'ok'){
                            toastr["info"]("虚拟机全部流表删除成功!");
                        }else{
                            toastr["error"]("虚拟机删除失败, " + response.reason);
                        }
                    },
                    error: function(jqXHR, textStatus, errorThrown){
                        toastr["error"]("请求不成功!");
                    },
                    dataType: 'json',
                    timeout: 2000
                });
            }
        });
    }
}