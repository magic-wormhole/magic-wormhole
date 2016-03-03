var vis, $; // hush

var container = document.getElementById("viz");
var options = {editable: false,
               showCurrentTime: false,
               snap: null,
               order: function(a,b) { return a.id - b.id; }
              };
var timeline = new vis.Timeline(container, options);
var items;

$.getJSON("data.json", function(data) {
    items = new vis.DataSet(data.items);
    timeline.setData({groups: new vis.DataSet(data.groups),
                      items: items});
    var start = data.items[0].start;
    var end = data.items[data.items.length-1].start;
    var span = end - start;
    timeline.setWindow(start - (span/10), end + (span/10));
    //timeline.fit(); // doesn't leave space on the ends
    timeline.setOptions({min: start - (span/10),
                         max: end + (span/10),
                         zoomMin: 50,
                         zoomMax: 1.2*span});
    var bar = timeline.addCustomTime(start, "cursor");
    timeline.on("timechange", update_cursor);
    update_cursor({time: new Date(start)});
    timeline.on("doubleClick", zoom);
    timeline.on("select", select_item);
    $.get("done", function(_) {});
});

function zoom(properties) {
    var target = properties.time.valueOf();
    var w = timeline.getWindow();
    var span = w.end - w.start;
    var new_span = span / 2;
    var new_start = target - new_span/2;
    var new_end = target + new_span/2;
    timeline.setWindow(new_start, new_end, {animation: true});
}

function update_cursor(properties) {
    var t = properties.time;
    document.getElementById("cursor_date").innerText = t;
    var m = vis.moment(t);
    document.getElementById("cursor_time").innerText = m.format("ss.SSSSSS");
}

function select_item(properties) {
    var item_id = properties.items[0];
    var i = items.get(item_id);
    if (i.end) {
        var elapsed = (i.end - i.start) / 1000;
        $("div#elapsed").text("elapsed: " + elapsed + " s");
    } else {
        $("div#elapsed").text("");
    }
}
