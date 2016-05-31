$(window).load(function(){setTimeout(function(){$("body").addClass("loaded")},200)});
$(".notification-button").dropdown({inDuration: 300, outDuration: 225, constrain_width: !1, hover: !0, gutter: 0, belowOrigin: !0, alignment: "left"});
$("select").not(".disabled").material_select();
/*Toaster配置*/
toastr.options={closeButton:!1,debug:!1,newestOnTop:!1,progressBar:!0,positionClass:"toast-top-right",preventDuplicates:!1,onclick:null,showDuration:"300",hideDuration:"1000",timeOut:"5000",extendedTimeOut:"1000",showEasing:"swing",hideEasing:"linear",showMethod:"fadeIn",hideMethod:"fadeOut"};

$(document).ready(function() {
	/*初始化顶部导航栏时钟*/
	var monthNames = [ "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December" ];
	var dayNames= ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
	var newDate = new Date();
	newDate.setDate(newDate.getDate());
    $("#Date").html(dayNames[newDate.getDay()]+" "+newDate.getDate()+" "+monthNames[newDate.getMonth()]+" "+newDate.getFullYear()),setInterval(function(){var e=(new Date).getSeconds();$("#sec").html((10>e?"0":"")+e)},1e3),setInterval(function(){var e=(new Date).getMinutes();$("#min").html((10>e?"0":"")+e)},1e3),setInterval(function(){var e=(new Date).getHours();$("#hours").html((10>e?"0":"")+e)},1e3);
    
    /*全屏按钮*/
    document.getElementById("full-screen-btn").addEventListener("click",function(){screenfull.enabled&&screenfull.request()});
    
});

(function($){
    $.fn.serializeJson=function(){
        var serializeObj={};
        var array=this.serializeArray();
        var str=this.serialize();
        $(array).each(function(){
            if(serializeObj[this.name]){
                if($.isArray(serializeObj[this.name])){
                    serializeObj[this.name].push(this.value);
                }else{
                    serializeObj[this.name]=[serializeObj[this.name],this.value];
                }
            }else{
                serializeObj[this.name]=this.value;
            }
        });
        return serializeObj;
    };
})(jQuery);