// Den Den Mushi: the RF bridge's enclosure, a transponder snail from One Piece,
// printed in resin (Anycubic Photon Mono M7, plate 223 x 126 x 230 mm).
//
// The snail carries the bridge: a Wemos D1 mini, the 433 MHz transmitter and
// receiver, and later an infrared emitter for the LED strips. Its eyes are the
// IR LEDs (the dome is the pupil), so the snail looks at what it drives; an IR
// receiver and a third LED look out of its chin, under the mouth.
//
// Parts:
//   body     foot, deck, neck, head and eye stalks in one piece, open underneath
//   shell    the shell, glued on the deck: a spiral on each side, the rotary dial on the right
//   handset  the receiver of the phone, glued on top of the shell
//   eye_right, eye_left   one per stalk, each holding a 5 mm IR LED looking straight ahead
//   cradle   the floor: every component sits in its own pocket and the wires
//            are soldered from part to part; four 6 x 2 mm magnets hold it in
//
// The 433 MHz antennas stay long (a quarter wave, about 17 cm): the transmitter's
// curls around inside the shell's rim, the receiver's along the inside of
// the foot wall, on its ledge. Resin lets the radio through.
//
// Export: set `part` (or -D 'part="body"') and render to STL. "assembly" shows
// everything in place, "exploded" lifts the parts apart, "cradle_fit" shows the
// cradle with its components.
//
// Everything is sized from nominal dimensions: measure the real parts and adjust
// the component block, the rest follows.

part = "assembly"; // [assembly, exploded, cradle_fit, section, step1, step2, step3, step4, step5, body_section, shell_section, eye_section, body, shell, handset, eye_right, eye_left, cradle]

/* ---------- components (nominal: measure yours) ---------- */
d1      = [34.2, 25.6, 1.0];   // Wemos D1 mini PCB, L x W x thickness; USB on a short edge
d1_top  = 3.6;                 // tallest part on top (the ESP-12 can)
d1_bot  = 1.6;                 // solder joints and parts under the board
usb     = [7.6, 2.8];          // micro-USB socket, W x H, on top of the board at the edge
plug    = [12, 8];             // micro-USB plug overmould, W x H: the tunnel lets it in
tx      = [19.5, 19.5, 8];     // 433 MHz transmitter (FS1000A), with its crystal
rx      = [30.5, 14.5, 7];     // 433 MHz receiver (XY-MK-5V)
tsop    = [6.0, 5.6, 8.0];     // IR receiver (TSOP38238 / VS1838B) body, W x D x H with its dome
ir_led  = 5.0;                 // IR LEDs (TSAL6200 or the like), 5 mm
ir_rim  = 5.9;                 // their flange
drive   = [16, 12];            // the IR driver: a transistor (2N2222 / AO3400) and two resistors, loose
magnet  = [6.4, 2.3];          // 6 x 2 mm disc magnets, the tower's, with clearance

/* ---------- body ---------- */
wall    = 2;
foot_h  = 20;                  // underside to deck
deck_t  = 2;
clr     = 0.3;                 // fit clearance
cradle_t = 2.5;
ledge_w = 1.6;                 // the receiver's antenna rests on a ledge inside the foot wall
ledge_drop = 4;                // that far under the deck
tail_x  = -68;                 // the foot runs from tail_x to nose_x
nose_x  = 64;

// the foot outline: a teardrop, tail narrow, front round
module foot_2d(inset = 0)
    offset(r = -inset) hull() {
        translate([-48, 0]) circle(r = 20, $fn = 72);   // wide enough for the D1 mini
        translate([-12, 0]) circle(r = 31, $fn = 96);
        translate([ 34, 0]) circle(r = 30, $fn = 96);
    }

// rounded top edge: the outline shrinks over the last `r` mm
module foot_solid(h, r, inset = 0) {
    steps = 6;
    hull() {
        linear_extrude(h - r) foot_2d(inset);
        for (i = [1:steps]) {
            a = 90 * i / steps;
            translate([0, 0, h - r + r * sin(a)])
                linear_extrude(0.01) foot_2d(inset + r * (1 - cos(a)));
        }
    }
}

/* ---------- head ---------- */
neck_at   = [38, 0, foot_h];
neck_r    = [14, 16];
head_at   = [50, 0, 58];
head_r    = [17, 19, 16];
stalk_len = 20;
stalk_r   = 4.5;
stalk_ch  = 2.8;               // wire channel through the stalk
peg       = [3.2, 4.5];        // stalk tip: r, length; the eye slides on it
eye_r     = 8.5;
stalk_tilt_fwd = 14;           // forward lean of the stalks
stalk_tilt_out = 20;           // and outward
stalk_base = [47, 8, 70];      // where the right stalk leaves the head (y mirrored for the left one)

module ellipsoid(r) scale(r) sphere(r = 1, $fn = 64);

// pose of a stalk: its base, then up along its axis
module stalk_frame(side) {
    translate([stalk_base.x, side * stalk_base.y, stalk_base.z])
        rotate([-side * stalk_tilt_out, stalk_tilt_fwd, 0]) children();
}

/* ---------- shell ---------- */
shell_R    = 33;                       // radius, seen from the side
shell_W    = 46;                       // thickness, left to right
shell_edge = 12;                       // rounding of the rim
shell_sink = 10;                       // how much of it the deck cuts off underneath
shell_c    = [-16, 0, foot_h + shell_R - shell_sink];
shell_wall = 2;
spiral_out = 19;                       // the spiral engraved on each side, from this radius
spiral_in  = [5, 10];                  // to this one: left side, right side (the dial sits there)
spiral_turns = [2.3, 1.4];
spiral_gap = 3.8;
spiral_depth = 2;
face_wall  = 4;                        // the sides are thicker: the spiral is cut into them
dial_r     = 9;
seat_r     = [21, 18];                 // the deck opening the shell plugs into, half-axes

// a puck with a rounded rim, its axis left-right (Y)
module puck(R, W, e)
    rotate([90, 0, 0]) rotate_extrude($fn = 128) hull() {
        translate([0, -W / 2]) square([0.01, W]);
        for (s = [-1, 1]) translate([R - e, s * (W / 2 - e)]) circle(r = e, $fn = 48);
    }

// the spiral groove on one face (y = side * W/2), drawn in the face's own plane
module spiral_groove(side, r_in, turns) {
    n = ceil(turns * 36);
    function pt(i) = let(t = i / n, a = 90 + side * t * turns * 360, r = spiral_out - (spiral_out - r_in) * t)
        [r * cos(a), r * sin(a)];
    translate([shell_c.x, side * (shell_W / 2 + 0.01), shell_c.z]) rotate([side * 90, 0, 0])
        for (i = [0:n - 1]) hull() for (q = [pt(i), pt(i + 1)])
            translate([q.x, q.y, -spiral_depth]) cylinder(d = spiral_gap, h = spiral_depth + 1, $fn = 12);
}

/* ---------- the parts ---------- */

module body() {
    difference() {
        union() {
            // foot, closed on top by the deck
            foot_solid(foot_h, 6);
            // neck and head
            hull() {
                translate(neck_at) scale([neck_r[0], neck_r[1], 1]) cylinder(r = 1, h = 1, $fn = 64);
                translate([head_at.x - 6, 0, head_at.z - 18]) ellipsoid([11, 13, 6]);
            }
            translate(head_at) ellipsoid(head_r);
            // lips: Den Den Mushi's big mouth, on the front of the head
            translate([head_at.x + head_r.x - 4, 0, head_at.z - 4])
                rotate([0, 90, 0]) scale([0.6, 1.25, 1]) rotate_extrude($fn = 64)
                    translate([8.5, 0]) scale([1.2, 1.4]) circle(r = 3.4, $fn = 24);
            // eye stalks
            for (s = [-1, 1]) stalk_frame(s) {
                translate([0, 0, -6]) cylinder(r = stalk_r, h = stalk_len + 6, $fn = 32);
                translate([0, 0, stalk_len]) cylinder(r = peg[0], h = peg[1], $fn = 32);
            }
            // chin: a flat face under the mouth for the IR receiver and the third LED
            hull() {
                translate([nose_x - 6, -13, 3]) cube([4, 26, 13]);
                translate([nose_x - 12, -15, 0]) cube([4, 30, 16]);
            }
        }
        // hollow foot, open underneath
        translate([0, 0, -0.01]) foot_solid(foot_h - deck_t, 4, wall);
        // hollow neck and head, open into the foot
        hull() {
            translate(neck_at - [0, 0, deck_t + 0.1]) scale([neck_r[0] - wall, neck_r[1] - wall, 1]) cylinder(r = 1, h = 1, $fn = 64);
            translate([head_at.x - 6, 0, head_at.z - 18]) ellipsoid([11 - wall, 13 - wall, 6]);
        }
        translate(head_at) ellipsoid(head_r - [wall, wall, wall]);
        // wire channels through the stalks, into the head
        for (s = [-1, 1]) stalk_frame(s)
            translate([0, 0, -12]) cylinder(d = stalk_ch, h = stalk_len + peg[1] + 13, $fn = 16);
        // mouth, a dark slit between the lips
        translate([head_at.x + head_r.x - 5, 0, head_at.z - 4])
            rotate([0, 90, 0]) scale([0.3, 1.1, 1]) cylinder(r = 7, h = 12, $fn = 48);
        // shell seat: an opening in the deck, the shell's plug drops in
        translate([shell_c.x, 0, foot_h - deck_t - 1])
            scale([seat_r[0], seat_r[1], 1]) cylinder(r = 1, h = deck_t + 2, $fn = 96);
        // USB tunnel through the tail: the plug's overmould goes in to the board
        translate([tail_x - 1, -plug.x / 2, cradle_t + usb_z() - plug.y / 2])
            cube([d1_x0() - tail_x + 1.5, plug.x, plug.y]);
        // chin: IR receiver window and the third LED
        translate([nose_x - 20, tsop_y - tsop.x / 2 - clr, cradle_t + 1])
            cube([30, tsop.x + 2 * clr, tsop.z - 1.5]);
        translate([nose_x - 20, led3_y, cradle_t + led3_z]) rotate([0, 90, 0])
            cylinder(d = ir_led + 2 * clr, h = 30, $fn = 32);
    }
    // ledge for the receiver's antenna, inside the foot wall just under the deck, all along
    translate([0, 0, foot_h - deck_t - ledge_drop]) linear_extrude(1.4) difference() {
        foot_2d(wall);
        foot_2d(wall + ledge_w);
    }
    // magnet bosses, standing up from the underside on the inside of the wall
    difference() {
        for (p = magnet_pts()) translate([p.x, p.y, cradle_t + clr])
            cylinder(r = magnet[0] / 2 + 2, h = foot_h - deck_t - cradle_t - clr, $fn = 32);
        for (p = magnet_pts()) translate([p.x, p.y, cradle_t + clr - 0.01])
            cylinder(d = magnet[0], h = magnet[1], $fn = 32);
    }
}

// the shell: a rounded puck standing on the deck, hollow, a spiral on each side,
// the dial on the right one, a cradle on top for the handset
module shell() {
    difference() {
        union() {
            intersection() {
                translate(shell_c) puck(shell_R, shell_W, shell_edge);
                translate([-200, -200, foot_h]) cube(400);
            }
            // plug that drops into the deck opening
            translate([shell_c.x, 0, foot_h - deck_t + 0.2])
                scale([seat_r[0] - clr, seat_r[1] - clr, 1]) cylinder(r = 1, h = deck_t, $fn = 96);
            // the handset's cradle: two forks on top
            for (x = [-fork_x, fork_x]) translate([shell_c.x + x, 0, shell_c.z + shell_R - 6])
                hull() {
                    scale([1, 1.4, 1]) cylinder(r = 5, h = 1, $fn = 32);
                    translate([0, 0, 9]) scale([1, 1.4, 1]) cylinder(r = 3.5, h = 1, $fn = 32);
                }
            dial();
        }
        // hollow, open underneath into the foot: the transmitter's antenna curls inside the rim
        translate(shell_c) puck(shell_R - shell_wall, shell_W - 2 * face_wall, shell_edge - shell_wall);
        translate([shell_c.x, 0, foot_h - deck_t - 1])
            scale([seat_r[0] - clr - shell_wall, seat_r[1] - clr - shell_wall, 1]) cylinder(r = 1, h = 20, $fn = 96);
        spiral_groove(-1, spiral_in[0], spiral_turns[0]);
        spiral_groove(1, spiral_in[1], spiral_turns[1]);
        // pegs of the handset
        for (x = [-fork_x, fork_x]) translate([shell_c.x + x, 0, fork_top() - 8]) cylinder(d = 3.4, h = 8.5, $fn = 20);
    }
}

// the handset's handle rests on the forks, its pegs in their holes
fork_x = 9;
function fork_top() = shell_c.z + shell_R + 4;
function handset_at() = [shell_c.x, 0, fork_top() - 2.5];

// rotary dial on the right side, at the end of the spiral
module dial() {
    translate([shell_c.x, shell_W / 2 - 0.5, shell_c.z]) rotate([-90, 0, 0]) difference() {
        cylinder(r = dial_r, h = 3, $fn = 64);
        for (i = [0:9]) rotate([0, 0, 50 + i * 26]) translate([dial_r - 3, 0, 1.6]) cylinder(d = 2.4, h = 2, $fn = 16);
        translate([0, 0, 2]) cylinder(r = 3, h = 2, $fn = 32);
    }
}

// the receiver of the phone: two cups and a handle; pegs underneath into the saddle
module handset() {
    difference() {
        union() {
            hull() for (x = [-26, 26]) translate([x, 0, 7]) sphere(r = 4.5, $fn = 32);
            for (x = [-26, 26]) translate([x, 0, 0]) hull() {
                translate([0, 0, 1]) scale([1, 1, 0.4]) sphere(r = 10, $fn = 48);
                translate([0, 0, 6]) sphere(r = 5, $fn = 32);
            }
        }
        // cups, slightly dished, with their holes
        for (x = [-26, 26]) translate([x, 0, -3.6]) cylinder(r = 7.5, h = 4, $fn = 48);
        for (x = [-26, 26]) for (a = [0:60:359]) translate([x + 3 * cos(a), 3 * sin(a), -2])
            cylinder(d = 1.4, h = 3, $fn = 12);
    }
    for (x = [-fork_x, fork_x]) translate([x, 0, 4]) rotate([180, 0, 0]) cylinder(d = 3, h = 9, $fn = 20);
}

// an eye: the LED goes in from the back, its dome is the pupil; the stalk's peg
// fits the socket underneath, the LED's legs go down the stalk
module eye(side = 1) {
    difference() {
        sphere(r = eye_r, $fn = 64);
        // undo the stalk's lean so the LED looks straight ahead
        rotate([side * stalk_tilt_out, 0, 0]) rotate([0, -stalk_tilt_fwd, 0]) rotate([0, 90, 0]) {
            cylinder(d = ir_led + 0.15, h = eye_r + 1, $fn = 32);               // to the front
            translate([0, 0, -eye_r - 1]) cylinder(d = ir_rim + 0.2, h = eye_r + 2.5, $fn = 32); // flange, from the back
        }
        translate([0, 0, -eye_r - 1]) cylinder(r = peg[0] + clr / 2, h = peg[1] + 1.5, $fn = 32);
    }
}

/* ---------- cradle: the floor, every component in its pocket ---------- */
function d1_x0() = tail_x + 11;                // the board's USB edge
function usb_z() = 2 + d1_bot + d1.z + usb.y / 2;  // centre of the USB socket over the cradle
tsop_y = -8;
led3_y = 8;
led3_z = 6;
function magnet_pts() = [[-20, 22], [-20, -22], [42, 18], [42, -18]];
tx_at    = [14, 2];               // front left, as far as possible from the receiver
rx_at    = [-10, -24];            // along the right side
drive_at = [-8, 6];

module fence(size, h = 3, open = []) {
    // a pocket wall of 1.2 mm around `size`, with gaps for the wires
    difference() {
        translate([-1.2, -1.2, 0]) cube([size.x + 2.4, size.y + 2.4, h]);
        translate([0, 0, -1]) cube([size.x, size.y, h + 2]);
        for (o = open) translate(o[0]) cube(o[1]);
    }
}

module cradle() {
    difference() {
        linear_extrude(cradle_t) foot_2d(wall + clr);
        for (p = magnet_pts()) translate([p.x, p.y, cradle_t - magnet[1] + 0.01])
            cylinder(d = magnet[0], h = magnet[1], $fn = 32);
        // feet: four shallow dimples for 8 mm rubber bumpers
        for (p = [[-48, 0], [-10, 22], [-10, -22], [44, 0]]) translate([p.x, p.y, -0.01])
            cylinder(d = 8.4, h = 0.6, $fn = 32);
    }
    translate([0, 0, cradle_t - 0.01]) {
        // D1 mini, flat, ESP up, USB to the tail; it stands on rails over its solder joints
        translate([d1_x0(), -d1.y / 2, 0]) {
            fence([d1.x, d1.y], h = 2 + d1_bot + d1.z, open = [[[8, -2, 1], [18, 30, 9]], [[-2, 6, 2], [4, d1.y - 12, 9]]]);
            for (y = [0.6, d1.y - 2.6]) translate([0, y, 0]) cube([d1.x, 2, 2 + d1_bot]);
        }
        // 433 MHz transmitter, at the front left, as far as possible from the receiver
        translate(tx_at) fence([tx.x, tx.y], open = [[[-2, 4, 1], [4, 12, 9]]]);
        // 433 MHz receiver, along the right side
        translate(rx_at) fence([rx.x, rx.y], open = [[[8, rx.y - 1, 1], [16, 4, 9]]]);
        // IR driver, loose parts in a tray
        translate(drive_at) fence(drive, h = 2, open = [[[-2, 3, 1], [4, 6, 9]]]);
        // IR receiver and the third LED, at the chin, looking out of their windows
        translate([nose_x - 9 - tsop.y, tsop_y - tsop.x / 2, 0])
            fence([tsop.y, tsop.x], h = 4, open = [[[-2, 1, 1], [4, tsop.x - 2, 9]], [[tsop.y - 1, 0, 1], [4, tsop.x, 9]]]);
        translate([nose_x - 16, led3_y, led3_z]) rotate([0, 90, 0]) difference() {
            union() {
                cylinder(d = ir_rim + 3, h = 3, $fn = 32);
                translate([0, -(ir_rim + 3) / 2, 0]) cube([led3_z, ir_rim + 3, 3]);
            }
            translate([0, 0, -1]) cylinder(d = ir_led + 0.2, h = 5, $fn = 32);
        }
    }
}

/* ---------- views ---------- */
c_body    = [0.86, 0.78, 0.68];
c_shell   = [0.55, 0.42, 0.62];
c_handset = [0.15, 0.15, 0.17];
c_eye     = [0.95, 0.95, 0.93];
c_cradle  = [0.30, 0.55, 0.40];

module components() {
    translate([0, 0, cradle_t]) {
        color([0.1, 0.25, 0.6]) translate([d1_x0(), -d1.y / 2, 2 + d1_bot]) {
            cube(d1);
            color("silver") translate([1, 4, d1.z]) cube([18, 16, d1_top - d1.z]);
            color("silver") translate([-1, (d1.y - usb.x) / 2, d1.z]) cube([5, usb.x, usb.y]);
        }
        color([0.1, 0.5, 0.2]) translate(tx_at) cube(tx);
        color([0.1, 0.5, 0.2]) translate(rx_at) cube(rx);
        color([0.1, 0.1, 0.1]) translate([nose_x - 9 - tsop.y, tsop_y - tsop.x / 2, 0]) cube([tsop.y, tsop.x, tsop.z]);
        color([0.6, 0.6, 0.9, 0.8]) translate([nose_x - 16, led3_y, led3_z]) rotate([0, 90, 0]) cylinder(d = ir_led, h = 8.6, $fn = 24);
    }
}

module placed_eyes(lift = 0) {
    for (s = [-1, 1]) stalk_frame(s) translate([0, 0, stalk_len + peg[1] / 2 + eye_r - 1.5 + lift])
        color(c_eye) eye(s);
}

module assembly(e = 0) {
    color(c_cradle) translate([0, 0, -e]) cradle();
    translate([0, 0, -e]) components();
    color(c_body) body();
    color(c_shell) translate([0, 0, e]) shell();
    color(c_handset) translate(handset_at() + [0, 0, 2 * e]) handset();
    placed_eyes(e);
}

/* ---------- assembly steps, for the renders ---------- */

// a wire through a list of points
module wire(pts, d = 1.4, c = [0.85, 0.25, 0.15]) color(c)
    for (i = [0:len(pts) - 2]) hull() for (q = [pts[i], pts[i + 1]]) translate(q) sphere(d = d, $fn = 10);

// inside half-width of the foot at x, a little in from the wall (the antenna's ledge)
function foot_hw(x) = max([for (c = [[-48, 20], [-12, 31], [34, 30]]) abs(x - c[0]) < c[1]
    ? sqrt(c[1] * c[1] - (x - c[0]) * (x - c[0])) : 0]) - wall - 1;

ant_z = foot_h - deck_t - ledge_drop + 1.6;
// transmitter: up through the deck opening, then around inside the shell's rim
tx_ant = concat([[tx_at.x + 2, tx_at.y + 3, cradle_t + 6], [tx_at.x - 6, 0, 14], [shell_c.x + 12, -3, foot_h + 4]],
    [for (a = [-40:-12:-320]) [shell_c.x + (shell_R - 6) * cos(a), -3, shell_c.z + (shell_R - 6) * sin(a)]]);
// receiver: out to the right wall, then along the ledge towards the tail and round it
rx_ant = concat([[rx_at.x + rx.x, rx_at.y + 4, cradle_t + 5], [rx_at.x + rx.x + 4, -foot_hw(24), ant_z]],
    [for (x = [20:-8:-60]) [x, -foot_hw(x), ant_z]], [[-64, -6, ant_z], [-64, 6, ant_z]]);

// IR LEDs in place, for the step views
module eye_leds() for (s = [-1, 1]) stalk_frame(s) translate([0, 0, stalk_len + peg[1] / 2 + eye_r - 1.5])
    rotate([s * stalk_tilt_out, 0, 0]) rotate([0, -stalk_tilt_fwd, 0]) rotate([0, 90, 0])
        color([0.6, 0.6, 0.95]) translate([0, 0, -6]) cylinder(d = ir_led, h = 8.6, $fn = 24);

module step(n) {
    if (n == 1) {          // eyes: LED in from the back, wires down the stalk, eye glued on the peg
        color(c_body) body();
        for (s = [-1, 1]) stalk_frame(s) translate([0, 0, stalk_len + peg[1] / 2 + eye_r - 1.5 + 22])
            color(c_eye) eye(s);
        for (s = [-1, 1]) stalk_frame(s) translate([0, 0, 22]) {
            translate([0, 0, stalk_len + peg[1] / 2 + eye_r - 1.5])
                rotate([s * stalk_tilt_out, 0, 0]) rotate([0, -stalk_tilt_fwd, 0]) rotate([0, 90, 0])
                    color([0.6, 0.6, 0.95]) translate([0, 0, -18]) cylinder(d = ir_led, h = 8.6, $fn = 24);
        }
        for (s = [-1, 1]) stalk_frame(s) wire([[0, 0, stalk_len + 40], [0, 0, -10]], d = 1.2);
    } else if (n == 2) {   // shell glued into the deck, handset on its forks
        color(c_body) body(); placed_eyes();
        color(c_shell) translate([0, 0, 25]) shell();
        color(c_handset) translate(handset_at() + [0, 0, 50]) handset();
    } else if (n == 3) {   // the cradle with its components
        color(c_cradle) cradle(); components();
    } else if (n == 4) {   // antennas: transmitter in the shell, receiver along the foot
        // cutaways: the foot without its deck, the shell cut in half
        color(c_body) intersection() { body(); translate([-200, -200, -1]) cube([400, 400, foot_h - deck_t + 0.5]); }
        color(c_shell) difference() { shell(); translate([-200, 0, -50]) cube(400); }
        color(c_cradle) cradle(); components();
        wire(tx_ant, c = [0.9, 0.3, 0.1]); wire(rx_ant, c = [0.1, 0.4, 0.9]);
    } else if (n == 5) {   // close from underneath: 4 magnets on each side
        color(c_body) body(); placed_eyes();
        color(c_shell) shell(); color(c_handset) translate(handset_at()) handset();
        translate([0, 0, -45]) { color(c_cradle) cradle(); components(); }
        for (p = magnet_pts()) color([0.75, 0.75, 0.8]) {
            translate([p.x, p.y, cradle_t + clr + 4]) cylinder(d = magnet[0] - 0.4, h = 2, $fn = 24);
            translate([p.x, p.y, cradle_t - magnet[1] - 41]) cylinder(d = magnet[0] - 0.4, h = 2, $fn = 24);
        }
    }
}

if (part == "assembly") assembly();
else if (part == "step1") step(1);
else if (part == "step2") step(2);
else if (part == "step3") step(3);
else if (part == "step4") step(4);
else if (part == "step5") step(5);
else if (part == "body_section") difference() { color(c_body) body(); translate([-200, 0, -50]) cube(400); }
else if (part == "shell_section") difference() { color(c_shell) shell(); translate([-200, 0, -50]) cube(400); }
else if (part == "eye_section") difference() { color(c_eye) eye(1); translate([-20, 0, -20]) cube(40); }
else if (part == "exploded") assembly(35);
else if (part == "section") difference() { assembly(); translate([-200, 0, -50]) cube(400); }
else if (part == "cradle_fit") { color(c_cradle) cradle(); components(); }
else if (part == "body") color(c_body) body();
else if (part == "shell") color(c_shell) shell();
else if (part == "handset") color(c_handset) handset();
else if (part == "eye_right") color(c_eye) eye(1);
else if (part == "eye_left") color(c_eye) eye(-1);
else if (part == "cradle") color(c_cradle) cradle();
