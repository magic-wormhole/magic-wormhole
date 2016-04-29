var d3, $; // hush

var container = d3.select("#viz");
var data;
var items;
var globals = {};

var zoom = d3.behavior.zoom().scaleExtent([1, Infinity]);
function zoomin() {
    //var w = Number(container.style("width").slice(0,-2));
    //console.log("zoomin", w);
    //zoom.center([w/2, 20]); // doesn't work yet
    zoom.scale(zoom.scale() * 2);
    globals.redraw();
}
function zoomout() {
    zoom.scale(zoom.scale() * 0.5);
    globals.redraw();
}

$.getJSON("data.json", function(d) {
    data = d;

    const LANE_HEIGHT = 30;
    const RECT_HEIGHT = 20;

    // the Y axis is as follows:
    // * each lane is LANE_HEIGHT tall (e.g. 30px)
    // * the actual rects are RECT_HEIGHT tall (e.g. 20px)
    // * there is a one-lane-tall gap at the top of the chart
    // * there are data.sides.length sides (e.g. one tx, one rx)
    // * there are data.lanes.length lanes (e.g. 6), each with a name
    // * there is a one-lane-tall gap between each side
    // * there is a one-lane-tall gap after the last lane
    // * the horizontal scale markers begin after that gap
    // * the tick marks extend another 6 pixels down

    var w = Number(container.style("width").slice(0,-2));

    function y_off(d) {
        return (LANE_HEIGHT * (d.side*(data.lanes.length+1) + d.lane)
                + d.wiggle);
    }
    var bottom_rule_y = LANE_HEIGHT * data.sides.length * (data.lanes.length+1);
    var bottom_y = bottom_rule_y + 45;

    var tip = d3.tip()
            .attr("class", "d3-tip")
            .html(function(d) { return "<span>" + d.details_str + "</span>"; })
            .direction("s")
    ;

    var chart = container.append("svg:svg")
            .attr("id", "outer_chart")
            .attr("width", w)
            .attr("pointer-events", "all")
            .call(zoom)
            .call(tip)
    ;
    //var chart_g = chart.append("svg:g");

    // this "backboard" rect lets us catch mouse events anywhere in the
    // chart, even between the bars. Without it, we only see events on solid
    // objects like bars and text, but not in the gaps between.
    chart.append("svg:rect")
        .attr("id", "outer_rect")
        .attr("width", w).attr("height", bottom_y).attr("fill", "none");

    // but the stuff we put inside it should have some room
    w = w-50;

    chart.selectAll("text.sides-label").data(data.sides).enter()
        .append("svg:text")
        .attr("class", "sides-label")
        .attr("x", "0px")
        .attr("y", function(d,idx) {
            return y_off({side: idx, lane: data.lanes.length/2,
                          wiggle: 0}) ;})
        .attr("text-anchor", "start") // anchor at top-left
        .attr("dy", ".71em")
        .attr("fill", "black")
        .text(function(d) { return d; })
    ;

    var lanes_by_sides = [];
    data.sides.forEach(function(side, side_index) {
        data.lanes.forEach(function(lane, lane_index) {
            lanes_by_sides.push({side: side, side_index: side_index,
                                 lane: lane, lane_index: lane_index});
        });
    });

    chart.selectAll("text.lanes-label").data(lanes_by_sides).enter()
        .append("svg:text")
        .attr("class", "lanes-label")
        .attr("x", "50px")
        .attr("y", function(d) {
            return y_off({side: d.side_index, lane: d.lane_index,
                          wiggle: 0}) ;})
        .attr("text-anchor", "start") // anchor at top-left
        .attr("dy", ".91em")
        .attr("fill", "#f88")
        .text(function(d) { return d.lane; })
    ;

    chart.append("svg:text")
        .attr("class", "seconds-label")
        //.attr("x", w/2).attr("y", y + 35)
        .attr("text-anchor", "middle")
        .attr("fill", "black")
        .text("seconds");

    d3.select("#outer_chart").attr("height", bottom_y);
    d3.select("#outer_rect").attr("height", bottom_y);
    d3.select("#zoom").attr("transform", "translate("+(w-10)+","+10+")");

    function reltime(t) {return t-data.bounds.min;}
    var last = data.bounds.max - data.bounds.min;
    //last = reltime(d3.max(data.dyhb, function(d){return d.finish_time;}));
    last = last * 1.05;
    // long downloads are likely to have too much info, start small
    if (last > 10.0)
        last = 10.0;
    // d3.time.scale() has no support for ms or us.
    var xOFF = d3.time.scale().domain([data.bounds.min, data.bounds.max])
                 .range([0,w]);
    var x = d3.scale.linear().domain([-last*0.05, last])
              .range([0,w]);
    zoom.x(x);
    function tx(d) { return "translate(" +x(d) + ",0)"; }
    function left(d) { return x(reltime(d.start_time)); }
    function left_server(d) { return x(reltime(d.server_sent)); }
    function right(d) {
        return d.finish_time ? x(reltime(d.finish_time)) : "1px";
    }
    function width(d) {
        return d.finish_time ? x(reltime(d.finish_time))-x(reltime(d.start_time)) : "1px";
    }
    function halfwidth(d) {
        if (d.finish_time)
            return (x(reltime(d.finish_time))-x(reltime(d.start_time)))/2;
        return "1px";
    }
    function middle(d) {
        if (d.finish_time)
                return (x(reltime(d.start_time))+x(reltime(d.finish_time)))/2;
            else
                return x(reltime(d.start_time)) + 1;
        }
    function color(d) { return data.server_info[d.serverid].color; }
    function servername(d) { return data.server_info[d.serverid].short; }
    function timeformat(duration) {
        // TODO: trim to microseconds, maybe humanize
        return duration;
    }

    function redraw() {
        // at this point zoom/pan must be fixed
        var min = data.bounds.min + x.domain()[0];
        var max = data.bounds.min + x.domain()[1];
        function inside(d) {
            var finish_time = d.finish_time || d.start_time;
            if (Math.max(d.start_time, min) <= Math.min(finish_time, max))
                return true;
            return false;
        }

        // from the data, build a list of bars, dots, and lines
        var clipped = {bars: [], dots: [], lines: []};
        data.items.filter(inside).forEach(function(d) {
            if (!d.finish_time) {
                clipped.dots.push(d);
            } else {
                clipped.bars.push(d);
                if (!!d.server_sent) {
                    clipped.lines.push(d);
                }
            }
        });
        globals.clipped = clipped;

        //chart.select(".dyhb-label")
        //    .attr("x", x(0))//"20px")
        //    .attr("y", y);

        // Panning and zooming will re-run this function multiple times, and
        // bars will come and go, so we must process all three selections
        // (including enter() and exit()).

        // TODO: add dots for events that have only start, not finish. Add
        // the server-sent bar (a vertical line, half height, centered
        // vertically) for events that have server-sent as well as finish.
        // This probably requires creating a dot for everything, but making
        // it invisible if finished is non-null, likewise for the server-sent
        // bar.

        // each item gets an SVG group (g.bars), translated left and down
        // to match the start time and side/lane of the event
        var bars = chart.selectAll("g.bars")
                .data(clipped.bars, function(d) { return d.start_time; })
                .attr("transform", function(d) {
                    return "translate("+left(d)+","+y_off(d)+")"; })
        ;
        // update the variable parts of each bar, which depends upon the
        // current pan/zoom values
        bars.select("rect")
            .attr("width", width);
        bars.select("text")
            .attr("x", halfwidth);
        bars.exit().remove();
        var new_bars = bars.enter()
                .append("svg:g")
                .attr("class", "bars")
                .attr("transform", function(d) {
                    return "translate("+left(d)+","+y_off(d)+")"; })
        ;

        // inside the group, we have a rect with a width for the duration of
        // the event, and a fixed height. The fill and stroke color depend
        // upon the event, and the title has the details. We append the rects
        // first, so the text is drawn on top (higher z-order)
        //y += 30*(1+d3.max(data.bars, function(d){return d.row;}));
        new_bars.append("svg:rect")
            .attr("width", width)
            .attr("height", RECT_HEIGHT)
            .attr("class", function(d) {
                var c = ["bar", "lane-" + d.lane];
                if (d.details.waiting)
                    c.push("wait-" + d.details.waiting);
                return c.join(" ");
            })
            .on("mouseover", function(d) {if (d.details_str) tip.show(d);})
            .on("mouseout", tip.hide)
            //.attr("title", function(d) {return d.details_str;})
        ;

        // each group also has a text, with 'x' set to place it in the middle
        // of the rect, and text contents that are drawn in the rect
        new_bars.append("svg:text")
            .attr("x", halfwidth)
            .attr("text-anchor", "middle")
            .attr("dy", "0.9em")
            //.attr("fill", "black")
            .text((d) => d.what)
            .on("mouseover", function(d) {if (d.details_str) tip.show(d);})
            .on("mouseout", tip.hide)
        ;

        // dots: events that have a single timestamp, rather than a range.
        // These get an SVG group, and a circle and some text.
        var dots = chart.selectAll("g.dots")
                .data(clipped.dots, (d) => d.start_time)
                .attr("transform",
                      (d) => "translate("+left(d)+","+(y_off(d)+LANE_HEIGHT/3)+")")
        ;
        dots.exit().remove();
        var new_dots = dots.enter()
                .append("svg:g")
                .attr("class", "dots")
                .attr("transform",
                      (d) => "translate("+left(d)+","+(y_off(d)+LANE_HEIGHT/3)+")")
        ;
        new_dots.append("svg:circle")
            .attr("r", "5")
            .attr("class", (d) => "dot lane-"+d.lane)
            .attr("fill", "#888")
            .attr("stroke", "black")
            .on("mouseover", function(d) {if (d.details_str) tip.show(d);})
            .on("mouseout", tip.hide)
        ;
        new_dots.append("svg:text")
            .attr("x", "5px")
            .attr("text-anchor", "start")
            .attr("dy", "0.2em")
            .text((d) => d.what)
            .on("mouseover", function(d) {if (d.details_str) tip.show(d);})
            .on("mouseout", tip.hide)
        ;

        // lines: these represent the time at which the server sent a message
        // which finished a bar. These get an SVG group, and a line
        var lines = chart.selectAll("g.lines")
                .data(clipped.lines, (d) => d.start_time)
                .attr("transform",
                      (d) => "translate("+left_server(d)+","+y_off(d)+")")
        ;
        lines.exit().remove();
        var new_lines = lines.enter()
                .append("svg:g")
                .attr("class", "lines")
                .attr("transform",
                      (d) => "translate("+left_server(d)+","+(y_off(d))+")")
        ;
        new_lines.append("svg:line")
            .attr("x1", 0).attr("y1", -5).attr("x2", "0").attr("y2", LANE_HEIGHT)
            .attr("class", (d) => "line lane-"+d.lane)
            .attr("stroke", "red")
        ;

        


        // horizontal scale markers: vertical lines at rational timestamps
        var rules = chart.selectAll("g.rule")
            .data(x.ticks(10))
            .attr("transform", tx);
        rules.select("text").text(x.tickFormat(10));

        var newrules = rules.enter().insert("svg:g")
              .attr("class", "rule")
              .attr("transform", tx)
        ;

        newrules.append("svg:line")
            .attr("class", "rule-tick")
            .attr("stroke", "black");
        chart.selectAll("line.rule-tick")
            .attr("y1", bottom_rule_y)
            .attr("y2", bottom_rule_y + 6);
        newrules.append("svg:line")
            .attr("class", "rule-red")
            .attr("stroke", "red")
            .attr("stroke-opacity", .3);
        chart.selectAll("line.rule-red")
            .attr("y1", 0)
            .attr("y2", bottom_rule_y);
        newrules.append("svg:text")
            .attr("class", "rule-text")
            .attr("dy", ".71em")
            .attr("text-anchor", "middle")
            .attr("fill", "black")
            .text(x.tickFormat(10));
        chart.selectAll("text.rule-text")
            .attr("y", bottom_rule_y + 9);
        rules.exit().remove();
        chart.select(".seconds-label")
            .attr("x", w/2)
            .attr("y", bottom_rule_y + 35);

    }
    globals.x = x;
    globals.redraw = redraw;

    zoom.on("zoom", redraw);

    d3.select("#zoom_in_button").on("click", zoomin);
    d3.select("#zoom_out_button").on("click", zoomout);
    d3.select("#reset_button").on("click",
                                  function() {
                                      x.domain([-last*0.05, last]).range([0,w]);
                                      redraw();
                                      });

    redraw();
    $.get("done", function(_) {});
});
