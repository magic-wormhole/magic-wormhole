var d3; // hush

var container = d3.select("#viz");
var data;
var items;
var globals = {};
var server_time_offset=0, rx_time_offset=0; // in seconds, relative to tx

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

function is_span(ev, category) {
    if (ev.category === category && !!ev.stop)
        return true;
    return false;
}
function is_event(ev, category) {
    if (ev.category === category && !ev.stop)
        return true;
    return false;
}

const server_message_color = {
    "welcome": 0, // receive

    "bind": 0, // send

    "allocate": 1, // send
    "allocated": 1, // receive

    "list": 2, // send
    "channelids": 2, // receive

    "claim": 3, // send
    "watch": 4, // send

    "deallocate": 5, // send
    "deallocated": 5, // receive

    "error": 6, // receive

    //"add": 8, // send (client message)
    //"message": 8, // receive (client message)

    "ping": 7, // send
    "pong": 7 // receive
};

const proc_map = {
    "command dispatch": "dispatch",
    "open websocket": "websocket",
    "code established": "code-established",
    "key established": "key-established",
    "transit connected": "transit-connected",
    "exit": "exit",
    "transit connect": "transit-connect",
    "import": "import"
 };

const TX_COLUMN = 14;
const RX_COLUMN = 18;
const SERVER_COLUMN0 = 20;
const SERVER_COLUMNS = [20,21,22,23,24,25];
const NUM_SERVER_COLUMNS = 6;
const MAX_COLUMN = 45;

function x_offset(offset, side_name) {
    if (side_name === "send")
        return offset;
    return MAX_COLUMN - offset;
}
function side_text_anchor(side_name) {
    if (side_name === "send")
        return "end";
    return "start";
}
function side_text_dx(side_name) {
    if (side_name === "send")
        return "-5px";
    return "5px";
}

d3.json("data.json", function(d) {
    data = d;

    // data is {send,receive}{fn,events}
    // each event has: {name, start, [stop], [server_rx], [server_tx],
    //                  [id], details={} }

    // Display all timestamps relative to the sender's startup event. If all
    // clocks are in sync, this will be the same as first_timestamp, but in
    // case they aren't, I'd rather use the sender as the reference point.
    var first = data.send.events[0].start;

    // The X axis is divided up into 50 slots, and then scaled to the screen
    // later. The left portion represents the "wormhole send" side, the
    // middle is the rendezvous server, and the right portion is the
    // "wormhole receive" side.
    //
    // 0: time axis, tick marks
    // 3: sender process events: import, dispatch, exit
    // 4: sender major application-level events: code/key establishment,
    //    transit-connect
    // 8: sender stalls: waiting for user, waiting for permission
    // 10: sender websocket transmits originate from here
    // 15: sender websocket receives terminate here

    // 20-25: rendezvous-server message lanes

    // 30: receiver websocket receives
    // 35: receiver websocket transmits
    // 37: receiver stalls
    // 41: receiver app-level events
    // 42: receiver process events

    var first_timestamp = Infinity;
    var last_timestamp = 0;
    function prepare_data(e, side_name) {
        var rel_e = {side_name: side_name, // send or receive
                     name: e.name,
                     start: e.start - first,
                     details: e.details
                    };
        if (e.stop) rel_e.stop = e.stop - first;
        if (side_name == "receive") {
            rel_e.start -= rx_time_offset;
            if (e.stop)
                rel_e.stop -= rx_time_offset;
        }
        if (rel_e.details.message) {
            if (rel_e.details.message.server_rx)
                rel_e.details.message.server_rx -= server_time_offset;
            if (rel_e.details.message.server_tx)
                rel_e.details.message.server_tx -= server_time_offset;
        }

        // sort events into categories, assign X coordinates to some
        if (proc_map[e.name]) {
            rel_e.category = "proc";
            rel_e.x = x_offset(3, side_name);
            if (e.name === "open websocket")
                rel_e.x = x_offset(4, side_name);
            rel_e.text = proc_map[e.name];
            if (e.name === "import")
                rel_e.text += " " + e.details.which;
        }
        if (e.details.waiting) {
            rel_e.category = "wait";
            var off = 8;
            if (e.details.waiting === "user")
                off += 0.5;
            rel_e.x = x_offset(off, side_name);
        }

        // also, calculate the overall time domain while we're at it
        [rel_e.start, rel_e.stop].forEach(v => {
            if (v) {
                if (v > last_timestamp) last_timestamp = v;
                if (v < first_timestamp) first_timestamp = v;
            }
        });
        return rel_e;
    }
    var events = data.send.events.map(e => prepare_data(e, "send"));
    events = events.concat(data.receive.events.map(e => prepare_data(e, "receive")));

    /* "Client messages" are ones that go all the way from one client to the
     other, through the rendezvous channel (and get echoed back to the sender
     too). We can correlate three websocket messages for each (the send, the
     local receive, and the remote receive) by comparing their "id" strings.

     Scan for all client messages, to build a list of central columns. For
     each message, we'll have tx/server_rx/server_tx/rx for the sending side,
     and server_rx/server_tx/rx for the receiving side. The "add" event
     contributes tx, the sender's echo contributes and the "message" event
     contributes server_rx, server_tx, and rx.
     */

    var side_map = new Map(); // side -> "send"/"receive"
    var c2c = new Map(); // msgid => {send,receive}{tx,server_rx,server_tx,rx}
    events.forEach(ev => {
        var id, phase;
        if (ev.name === "ws_send") {
            if (ev.details.type !== "add")
                return;
            id = ev.details.id;
            phase = ev.details.phase;
            side_map.set(ev.details._side, ev.side_name);
        } else if (ev.name === "ws_receive") {
            if (ev.details.message.type !== "message")
                return;
            id = ev.details.message.id;
            phase = ev.details.message.phase;
        } else
            return;

        if (!c2c.has(id)) {
            c2c.set(id, {phase: phase,
                         side_id: ev.details._side,
                         //tx_side_name: assigned when we see 'add'
                         id: id,
                         arrivals: []
                         //col, server_x: assigned later
                         //server_rx: assigned when we see 'message'
                        });
        }
        var cm = c2c.get(id);
        if (ev.name === "ws_send") { // add
            cm.tx = ev.start;
            cm.tx_x = x_offset(TX_COLUMN, ev.side_name);
            cm.tx_side_name = ev.side_name;
        } else { // message
            cm.server_rx = ev.details.message.server_rx - first;
            cm.arrivals.push({server_tx: ev.details.message.server_tx - first,
                              rx: ev.start,
                              rx_x: x_offset(RX_COLUMN, ev.side_name)});
        }
    });

    // sort c2c messages by initial sending time
    var client_messages = Array.from(c2c.values());
    client_messages.sort( (a,b) => (a.tx - b.tx) );

    // assign columns
    // TODO: identify overlaps between the c2c messages, share columns
    // between messages which don't overlap

    client_messages.forEach((cm,index) => {
        cm.col = index % 6;
        cm.server_x = 20 + cm.col;
    });

    console.log("client_messages", client_messages);
    console.log(side_map);
    console.log(first_timestamp, last_timestamp);

    /* "Server messages" are ones that stop or originate at the rendezvous
     server. These are of types other than "add" or "message". Although many
     of these provoke responses, we do not attempt to correlate these with
     any other message. For outbound ws_send messages, we know the send
     timestamp, but not the server receipt timestamp. For inbound ws_receive
     messages, we know both.
     */
    var outbound_sm = new Map();
    globals.outbound_sm = outbound_sm;
    events
        .filter(ev => ev.name === "ws_send")
        .forEach(ev => {
            // we don't know the server receipt time, so draw a horizontal
            // line by setting stop_timestamp=start_timestamp
            var sm = {side_name: ev.side_name,
                      start_timestamp: ev.start,
                      stop_timestamp: ev.start,
                      start_x: x_offset(TX_COLUMN, ev.side_name),
                      end_x: x_offset(20, ev.side_name),
                      text_x: x_offset(TX_COLUMN, ev.side_name),
                      text_timestamp: ev.start,
                      text_dy: "-5px",
                      type: ev.details.type,
                      tip: ev.details.type,
                      ev: ev
                     };
            outbound_sm.set(ev.details.id, sm);
        });

    events
        .filter(ev => ev.name === "ws_receive")
        .filter(ev => ev.details.message.type === "ack")
        .forEach(ev => {
            var id = ev.details.message.id;
            var server_tx = ev.details.message.server_tx;
            var sm = outbound_sm.get(id);
            sm.stop_timestamp = server_tx - first;
        });

    var server_messages = [];
    events
        .filter(ev => ev.name === "ws_receive")
        .filter(ev => ev.details.message.type !== "message")
        .filter(ev => ev.details.message.type !== "ack")
        .forEach(ev => {
            var sm = {side_name: ev.side_name,
                      start_timestamp: ev.details.message.server_tx - first,
                      stop_timestamp: ev.start,
                      start_x: x_offset(20, ev.side_name),
                      end_x: x_offset(RX_COLUMN, ev.side_name),
                      text_x: x_offset(RX_COLUMN, ev.side_name),
                      text_timestamp: ev.start,
                      text_dy: "8px",
                      type: ev.details.message.type,
                      tip: ev.details.message.type,
                      ev: ev
                     };
            server_messages.push(sm);
        });
    server_messages = server_messages.concat(
        Array.from(outbound_sm.values())
            .filter(sm => sm.type !== "add"));
    console.log("server_messages", server_messages);

    // TODO: this goes off the edge of the screen, use the viewport instead
    var container_width = Number(container.style("width").slice(0,-2));
    var container_height = Number(container.style("height").slice(0,-2));
    container_height = 700; // no contents, so no height is allocated yet
    // scale the X axis to the full width of our container
    var x = d3.scale.linear().domain([0, 50]).range([0, container_width]);

    // scale the Y axis later
    var y = d3.scale.linear().domain([first_timestamp, last_timestamp])
            .range([0, container_height]);
    zoom.y(y);
    zoom.on("zoom", redraw);


    var tip = d3.tip()
            .attr("class", "d3-tip")
            .html(function(d) { return "<span>" + d + "</span>"; })
            .direction("s")
    ;

    var chart = container.append("svg:svg")
            .attr("id", "outer_chart")
            .attr("width", container_width)
            .attr("height", container_height)
            .attr("pointer-events", "all")
            .call(zoom)
            .call(tip)
    ;

    var defs = chart.append("svg:defs");
    defs.append("svg:marker")
        .attr("id", "markerCircle")
        .attr("markerWidth", 8)
        .attr("markerHeight", 8)
        .attr("refX", 5)
        .attr("refY", 5)
        .append("circle")
        .attr("cx", 5)
        .attr("cy", 5)
        .attr("r", 3)
        .attr("style", "stroke: none; fill: #000000;")
    ;
    defs.append("svg:marker")
        .attr("id", "markerArrow")
        .attr("markerWidth", 26)
        .attr("markerHeight", 26)
        .attr("refX", 26)
        .attr("refY", 12)
        .attr("orient", "auto")
        .attr("markerUnits", "userSpaceOnUse") // don't scale to stroke-width
        .append("path")
        .attr("d", "M8,20 L20,12 L8,4")
        .attr("style", "stroke: #000000; fill: none")
    ;

    chart.append("svg:line")
        .attr("x1", x(0.5)).attr("y1", 0)
        .attr("x2", x(0.5)).attr("y2", container_height)
        .attr("class", "y_axis")
    ;
    chart.append("svg:g")
        .attr("class", "seconds_g")
        .attr("transform", "translate("+(x(0.5)+5)+","+(container_height-10)+")")
        .append("svg:text")
        .text("seconds")
    ;

    chart.append("svg:line")
        .attr("x1", x(TX_COLUMN)).attr("y1", y(first_timestamp))
        .attr("x2", x(TX_COLUMN)).attr("y2", y(last_timestamp))
        .attr("class", "client_tx")
    ;
    chart.append("svg:text")
        .attr("x", x(TX_COLUMN)).attr("y", 10)
        .attr("text-anchor", "middle")
        .text("sender tx");

    chart.append("svg:line")
        .attr("x1", x(RX_COLUMN)).attr("y1", y(first_timestamp))
        .attr("x2", x(RX_COLUMN)).attr("y2", y(last_timestamp))
        .attr("class", "client_rx")
    ;
    chart.append("svg:text")
        .attr("x", x(RX_COLUMN)).attr("y", 10)
        .attr("text-anchor", "middle")
        .text("sender rx");

    chart.selectAll("line.c2c_column").data(SERVER_COLUMNS)
        .enter().append("svg:line")
        .attr("class", "c2c_column")
        .attr("x1", d => x(d)).attr("y1", y(first_timestamp))
        .attr("x2", d => x(d)).attr("y2", y(last_timestamp))
    ;

    chart.append("svg:line")
        .attr("x1", x(MAX_COLUMN-RX_COLUMN)).attr("y1", y(first_timestamp))
        .attr("x2", x(MAX_COLUMN-RX_COLUMN)).attr("y2", y(last_timestamp))
        .attr("class", "client_rx")
    ;
    chart.append("svg:text")
        .attr("x", x(MAX_COLUMN-RX_COLUMN)).attr("y", 10)
        .attr("text-anchor", "middle")
        .text("receiver rx");

    chart.append("svg:line")
        .attr("x1", x(MAX_COLUMN-TX_COLUMN)).attr("y1", y(first_timestamp))
        .attr("x2", x(MAX_COLUMN-TX_COLUMN)).attr("y2", y(last_timestamp))
        .attr("class", "client_tx")
    ;
    chart.append("svg:text")
        .attr("x", x(MAX_COLUMN-TX_COLUMN)).attr("y", 10)
        .attr("text-anchor", "middle")
        .text("receiver tx");

    // produces list of {p_from, p_to, col, add_arrow, tip}
    function cm_line(cm) {
        // We draw a bunch of two-point lines
        var lines = [];
        function push(p_from, p_to, add_arrow) {
            lines.push({p_from: p_from, p_to: p_to,
                        col: cm.col, tip: cm.tip,
                        add_arrow: add_arrow});
        }
        // the first goes from the sender to the server_rx, if we know it
        // TODO: tolerate not knowing it
        var sender_point = [cm.tx_x, cm.tx];
        var server_rx_point = [cm.server_x, cm.server_rx];
        push(sender_point, server_rx_point, true);

        // the second goes from the server_rx to the last server_tx
        var last_server_tx = Math.max.apply(null,
                                            cm.arrivals.map(a => a.server_tx));
        var last_server_tx_point = [cm.server_x, last_server_tx];
        push(server_rx_point, last_server_tx_point, false);

        cm.arrivals.forEach(ar => {
            var delivery_tx_point = [cm.server_x, ar.server_tx];
            var delivery_rx_point = [ar.rx_x, ar.rx];
            push(delivery_tx_point, delivery_rx_point, true);
        });

        return lines;
    }

    var all_cm_lines = [];
    client_messages.forEach(v => {
        all_cm_lines = all_cm_lines.concat(cm_line(v));
    });
    console.log(all_cm_lines);
    var cm_colors = d3.scale.category10();
    chart.selectAll("line.c2c").data(all_cm_lines)
        .enter()
        .append("svg:line")
        .attr("class", "c2c") // circle-arrow-circle")
        .attr("stroke", ls => cm_colors(ls.col))
        .attr("style", ls => {
            if (ls.add_arrow) return "marker-end: url(#markerArrow);";
            return "";
        })
        .on("mouseover", ls => {
            if (ls.tip)
                tip.show(ls.tip);
            chart.selectAll("circle.c2c").filter(d => d.col == ls.col)
                .attr("r", 10);
            chart.selectAll("line.c2c")
                .classed("active", d => d.col == ls.col);
        })
        .on("mouseout", ls => {
            tip.hide(ls);
            chart.selectAll("circle.c2c")
                .attr("r", 5);
            chart.selectAll("line.c2c")
                .classed("active", false);
        })
    ;

    chart.selectAll("g.c2c").data(client_messages)
        .enter()
        .append("svg:g")
        .attr("class", "c2c")
        .append("svg:text")
        .attr("class", "c2c")
        .attr("text-anchor", cm => side_text_anchor(cm.tx_side_name))
        .attr("dx", cm => side_text_dx(cm.tx_side_name))
        .attr("dy", "10px")
        .attr("fill", cm => cm_colors(cm.col))
        .text(cm => cm.phase);

    function cm_dot(cm) {
        var dots = [];
        var color = cm_colors(cm.col);
        var tip = cm.phase;
        function push(x,y) {
            dots.push({x: x, y: y, col: cm.col, color: color, tip: tip});
        }
        push(cm.tx_x, cm.tx);
        cm.arrivals.forEach(ar => push(ar.rx_x, ar.rx));
        return dots;
    }
    var all_cm_dots = [];
    client_messages.forEach(cm => {
        all_cm_dots = all_cm_dots.concat(cm_dot(cm));
    });
    chart.selectAll("circle.c2c").data(all_cm_dots)
        .enter()
        .append("svg:circle")
        .attr("class", "c2c")
        .attr("r", 5)
        .attr("fill", dot => dot.color)
        .on("mouseover", dot => {
            if (dot.tip)
                tip.show(dot.tip);
            chart.selectAll("circle.c2c").filter(d => d.col == dot.col)
                .attr("r", 10);
            chart.selectAll("line.c2c")
                .classed("active", d => d.col == dot.col);
        })
        .on("mouseout", dot => {
            tip.hide(dot);
            chart.selectAll("circle.c2c")
                .attr("r", 5);
            chart.selectAll("line.c2c")
                .classed("active", false);
        })
    ;

    // server messages
    chart.selectAll("line.server-message").data(server_messages)
        .enter()
        .append("svg:line")
        .attr("class", "server-message")
        .attr("stroke", sm => cm_colors(server_message_color[sm.type] || 0))
        .attr("style", "marker-end: url(#markerArrow)")
        .on("mouseover", sm => {
            if (sm.tip)
                tip.show(sm.tip);
        })
        .on("mouseout", sm => {
            tip.hide(sm);
        })
    ;
    chart.selectAll("g.server-message").data(server_messages)
        .enter()
        .append("svg:g")
        .attr("class", "server-message")
    .append("svg:text")
        .attr("class", "server-message")
        .attr("text-anchor", sm => side_text_anchor(sm.side_name))
        .attr("dx", sm => side_text_dx(sm.side_name))
        .attr("dy", sm => sm.text_dy)
        .attr("fill", sm => cm_colors(server_message_color[sm.type] || 0))
        .text(sm => sm.type);
    // TODO: add dots on the known send/receive time points

    var w = chart.selectAll("g.wait")
        .data(events.filter(ev => ev.category === "wait"))
        .enter().append("svg:g")
        .attr("class", "wait");
    w.append("svg:rect")
        .attr("class", ev => "wait wait-"+ev.details.waiting)
        .attr("width", 10);
    var wt = chart.selectAll("g.wait-text")
        .data(events.filter(ev => ev.category === "wait"))
        .enter().append("svg:g")
        .attr("class", "wait-text");
    wt.append("svg:text")
        .attr("class", ev => "wait-text wait-text-"+ev.details.waiting)
        .attr("text-anchor", ev => ev.side_name === "send" ? "end" : "start")
        .attr("dx", ev => ev.side_name === "send" ? "-5px" : "15px")
        .attr("dy", "5px")
        .text(v => v.name+" ("+v.details.waiting+")");

    // process-related events
    var pe = chart.selectAll("g.proc-event")
        .data(events.filter(ev => is_event(ev, "proc")))
        .enter().append("svg:g")
        .attr("class", "proc-event");
    pe.append("svg:circle")
        .attr("class", ev => "proc-event proc-event-"+proc_map[ev.name])
        .attr("cx", ev => ev.side_name === "send" ? "12px" : "-2px")
        .attr("r", 5)
        .attr("fill", "red")
        .attr("width", 10);
    pe.append("svg:text")
        .attr("class", ev => "proc-event proc-event-"+proc_map[ev.name])
        .attr("text-anchor", ev => ev.side_name === "send" ? "start" : "end")
        .attr("dx", ev => ev.side_name === "send" ? "15px" : "-5px")
        .attr("dy", "5px")
        .attr("transform", "rotate(-30)")
        .text(ev => proc_map[ev.name]);

    // process-related spans
    var ps = chart.selectAll("g.proc-span")
        .data(events.filter(ev => is_span(ev, "proc")))
        .enter().append("svg:g")
        .attr("class", "proc-span");
    ps.append("svg:rect")
        .attr("class", ev => "proc-span proc-span-"+proc_map[ev.name])
        .attr("width", 10);
    var pst = chart.selectAll("g.proc-span-text")
        .data(events.filter(ev => is_span(ev, "proc")))
        .enter().append("svg:g")
        .attr("class", "proc-span-text");
    pst.append("svg:text")
        .attr("class", ev => "proc-span-text proc-span-text-"+proc_map[ev.name])
        .attr("text-anchor", ev => ev.side_name === "send" ? "start" : "end")
        .attr("dx", ev => ev.side_name === "send" ? "15px" : "-5px")
        .attr("dy", "5px")
        .text(ev => ev.text);

    function ty(d) { return "translate(0,"+y(d)+")"; }

    function redraw() {
        chart.selectAll("line.c2c")
            .attr("x1", ls => x(ls.p_from[0]))
            .attr("y1", ls => y(ls.p_from[1]))
            .attr("x2", ls => x(ls.p_to[0]))
            .attr("y2", ls => y(ls.p_to[1]))
        ;
        chart.selectAll("g.c2c")
            .attr("transform", cm =>
                  "translate("+x(cm.tx_x)+","+y(cm.tx)+")")
        ;
        chart.selectAll("circle.c2c")
            .attr("cx", d => x(d.x))
            .attr("cy", d => y(d.y))
        ;
        chart.selectAll("line.server-message")
            .attr("x1", sm => x(sm.start_x))
            .attr("y1", sm => y(sm.start_timestamp))
            .attr("x2", sm => x(sm.end_x))
            .attr("y2", sm => y(sm.stop_timestamp));
        chart.selectAll("g.server-message")
            .attr("transform", sm => {
                return "translate("+x(sm.text_x)+","+y(sm.text_timestamp)+")";
            })
        ;


        chart.selectAll("g.wait")
            .attr("transform", ev => {
                return "translate("+x(ev.x)+","+y(ev.start)+")";
            });
        chart.selectAll("rect.wait")
            .attr("height", ev => y(ev.stop)-y(ev.start));

        chart.selectAll("g.wait-text")
            .attr("transform", ev => {
                return "translate("+x(ev.x)+","+y((ev.start+ev.stop)/2)+")";
            });

        chart.selectAll("g.proc-event")
            .attr("transform", ev => {
                return "translate("+x(ev.x)+","+y(ev.start)+")";
            })
        ;

        chart.selectAll("g.proc-span")
            .attr("transform", ev => {
                return "translate("+x(ev.x)+","+y(ev.start)+")";
            })
        ;
        chart.selectAll("rect.proc-span")
            .attr("height", ev => y(ev.stop)-y(ev.start));
        chart.selectAll("g.proc-span-text")
            .attr("transform", ev => {
                return "translate("+x(ev.x)+","+y((ev.start+ev.stop)/2)+")";
            });


        // vertical scale markers: horizontal tick lines at rational
        // timestamps

        // TODO: clicking on a dot should set the new zero time
        var rules = chart.selectAll("g.rule")
                .data(y.ticks(10))
                .attr("transform", ty);
        rules.select("text")
            .text(t => y.tickFormat(10, "s")(t)+"s");
        var newrules = rules.enter().insert("svg:g")
                .attr("class", "rule")
                .attr("transform", ty)
        ;
        newrules.append("svg:line")
            .attr("class", "rule-tick")
            .attr("stroke", "black");
        chart.selectAll("line.rule-tick")
            .attr("x1", x(0.5)-5)
            .attr("x2", x(0.5));
        newrules.append("svg:line")
            .attr("class", "rule-red")
            .attr("stroke", "red")
            .attr("stroke-opacity", .3);
        chart.selectAll("line.rule-red")
            .attr("x1", x(0.5))
            .attr("x2", x(MAX_COLUMN));
        newrules.append("svg:text")
            .attr("class", "rule-text")
            .attr("dx", ".1em")
            .attr("dy", "-0.2em")
            .attr("text-anchor", "start")
            .attr("fill", "black")
            .text(t => y.tickFormat(10, "s")(t)+"s");
        chart.selectAll("text.rule-text")
            .attr("x", 6 + 9);
        rules.exit().remove();
    }


    redraw();
});

/*
TODO

* identify the largest gaps in the timeline (biggest is probably waiting for
  the recipient to start the program, followed by waiting for recipient to
  type in code, followed by waiting for recipient to approve transfer, with
  the time of actual transfer being anywhere among the others).
* identify groups of events that are separated by those gaps
* put a [1 2 3 4 all] set of buttons at the top of the page
* clicking on each button will zoom the display to 10% beyond the span of
  events in the given group, or reset the zoom to include all events

*/


function OFF() {
    /* leftover code from an older implementation, retained since there might
    still be some useful pieces here */


    function y_off(d) {
        return (LANE_HEIGHT * (d.side*(data.lanes.length+1) + d.lane)
                + d.wiggle);
    }
    var bottom_rule_y = LANE_HEIGHT * data.sides.length * (data.lanes.length+1);
    var bottom_y = bottom_rule_y + 45;
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

    function oldredraw() {
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
        var linedata = clipped.lines.map(d => [
            [d.server_sent, 0],
            [d.server_sent, LANE_HEIGHT],
            [d.finish_time, 0],
        ]);

        function lineshape(d) {
            var l = d3.svg.line()
                    .x(d => x(d[0]))
                    .y(d => y_off(d) + 12345);
        }
        function update_line(sel) {
            sel.attr("d", lineshape)
                .attr("class", d => "line lane-"+d.lane)
            ;
        }

        var lines = chart.selectAll("polyline.lines")
                .data(linedata)

                .attr("transform",
                      (d) => "translate("+left(d)+","+y_off(d)+")")
        ;
        lines.exit().remove();
        var new_lines = lines.enter()
                .append("svg:g")
                .attr("class", "lines")
                .attr("transform",
                      (d) => "translate("+left_server(d)+","+(y_off(d))+")")
        ;
        new_lines.append("svg:line")
            .attr("x1", 0)
            .attr("y1", -5)
            .attr("x2", "0")
            .attr("y2", LANE_HEIGHT)
            .attr("class", (d) => "line lane-"+d.lane)
            .attr("stroke", "red")
        ;
        new_lines.append("svg:line")
            .attr("x1", 0).attr("y1", -5)
            .attr("x2", (d) => x(d.finish_time - d.server_sent))
            .attr("y2", 0)
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
}
