// Shanks' Den Den Mushi: the parts made in Blender (shanks.py, cape.py, sword.py, tech.py)
// and the cradle (berceau.scad), put together for the renders and the assembly guide.
// The snail looks towards -y, z up, its left flank at +x.
include <berceau.scad>

part = "assembly"; // [assembly, exploded, body, cradle, cape, sword, body_section, eye_section, step1..step6]

c_body  = [0.93, 0.80, 0.62];
c_hat   = [0.95, 0.80, 0.34];
c_cape  = [0.13, 0.13, 0.15];
c_sword = [0.20, 0.18, 0.17];
c_cradle = [0.30, 0.55, 0.40];

module body()  import("out/body_hollow.stl");
module hat()   import("out/hat.stl");
module cape()  import("out/cape.stl");
module sword() import("out/sword.stl");

// the hat in its own colour, a hair over the body it is part of
module painted_body() {
    color(c_body) body();
    color(c_hat) translate([17, 4, 76]) scale(1.006) translate([-17, -4, -76]) hat();
}

module wire(pts, d = 1.3, c = [0.9, 0.3, 0.1]) color(c)
    for (i = [0:len(pts) - 2]) hull() for (q = [pts[i], pts[i + 1]]) translate(q) sphere(d = d, $fn = 10);

// the 433 MHz antennas, about 17 cm each: the transmitter's up into the shell and round its inside,
// the receiver's back along the left wall and round the tail
tx_ant = concat([[24, 18, 11], [20, 30, 20], [17, 38, 30]],
                [for (a = [200:-14:-110]) [17, 56 + 24 * cos(a), 54 + 24 * sin(a)]]);
rx_ant = concat([[8, 16, 18], [2, 24, 12]], [for (y = [30:8:78]) [1 + (y - 30) * 0.12, y, 10]], [[12, 84, 9], [22, 84, 9]]);

module eye_leds(lift = 0) for (x = [8, 26]) color([0.35, 0.35, 0.8])
    translate([x, -9 + lift, 57]) rotate([90, 0, 0]) translate([0, 0, -6]) cylinder(d = 3, h = 5.3, $fn = 20);

module assembly(e = 0) {
    painted_body();
    color(c_cape) translate([0, 0, e]) cape();
    color(c_sword) translate([e, 0, 0]) sword();
    color(c_cradle) translate([0, 0, -e]) cradle();
    translate([0, 0, -e]) parts();
}

module halve() difference() { children(); translate([17, -100, -10]) cube([200, 300, 200]); }

if (part == "assembly") assembly();
else if (part == "exploded") assembly(35);
else if (part == "body") color(c_body) body();
else if (part == "cradle") color(c_cradle) cradle();
else if (part == "cape") color(c_cape) cape();
else if (part == "sword") color(c_sword) sword();
else if (part == "body_section") halve() color(c_body) body();
else if (part == "eye_section") { halve() color(c_body) body(); eye_leds(); }
else if (part == "step1") { halve() color(c_body) body(); eye_leds(14); }                   // LEDs into the eyes, from inside
else if (part == "step2") { color(c_cradle) cradle(); parts(); }                           // the cradle with its parts
else if (part == "step3") { halve() painted_body(); color(c_cradle) cradle(); parts(); wire(tx_ant); wire(rx_ant, c = [0.1, 0.4, 0.9]); }
else if (part == "step4") { painted_body(); color(c_cape) translate([0, 0, 25]) cape(); color(c_sword) translate([25, 0, 0]) sword(); }
else if (part == "step5") { painted_body(); color(c_cape) cape(); color(c_sword) sword();
                            translate([0, 0, -40]) { color(c_cradle) cradle(); parts(); }
                            for (b = bosses) color([0.75, 0.75, 0.8]) { translate([b.x, b.y, plate + 2]) cylinder(d = 6, h = 2, $fn = 24);
                                                                        translate([b.x, b.y, plate - 2.3 - 40]) cylinder(d = 6, h = 2, $fn = 24); } }
