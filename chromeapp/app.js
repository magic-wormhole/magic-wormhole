console.log("in app.js");
// this fails when the app name has a hyphen in it. Underscore is ok.
var port = chrome.runtime.connectNative("com.lothar.magic.wormhole.helper");
port.onMessage.addListener(function(msg) {
    console.log("port rx:", msg);
});
port.onDisconnect.addListener(function() {
    console.log("port disconnected");
});

d3.select("#go").on("click", function(ev) {
    d3.select("#log").append("li").text("went");
    port.postMessage({ text: "hello helper" });
});

// Support dropping a single file onto this app.
var dnd = new DnDFileController('body', function(data) {
    console.log("drop");
    d3.select("#log").append("li").text("dnd");
    console.log(data);
    console.log(data.files);
    
    for (var i = 0; i < data.items.length; i++) {
        var item = data.items[i];
        console.log(item.kind, item.type);
        if (item.kind == 'file' &&
            item.type.match('text/*') &&
            item.webkitGetAsEntry()) {
            //chosenEntry = item.webkitGetAsEntry();
            continue;
        }
    }
    console.log("data.files");
    for (i=0; i<data.files.length; i++) {
        var f = data.files[i];
        console.log(f);
        d3.select("#log").append("li").text(""+f.size+" "+f.type+" "+f.name);
        // f.name, f.lastModified, f.size, f.type
        var reader = new FileReader();
        reader.onload = function(e) {
            console.log(e.target.result);
            d3.select("img#pix").attr("src", e.target.result);
        };
        //reader.readAsArrayBuffer(f);
        reader.readAsDataURL(f);
    }

});

//setup();
//window.onload = setup;
