// The cradle of Shanks' Den Den Mushi: the floor that closes the hollow snail and carries the
// electronics. Its outline and the magnet bosses come from tech.py (cradle_outline.scad).
// The snail looks towards -y, z up; the cradle fills the opening from z = 0 to floor_z.
//
//   openscad -D 'part="cradle"' -o out/cradle.stl berceau.scad
//   part = "fit": the cradle with the components, to check against the body
include <out/cradle_outline.scad>

part = "cradle"; // [cradle, fit, parts]
trim = true;     // cut the pocket walls with the real body (slow): they lean in above the floor

clr    = 0.3;            // all round, in the opening
gap    = 0.3;            // under the magnet bosses
plate  = floor_z - gap;  // the plate's thickness
magnet = [6.4, 2.3];     // 6 x 2 mm discs, with clearance
fence_t = 1.2;

/* ---------- components (nominal: measure yours) ---------- */
d1   = [25.6, 34.2, 1.0];   // Wemos D1 mini, lying flat, USB towards the tail (+y)
d1_bot = 1.6;               // its solder joints underneath: they sit in a recess of the plate
d1_at = [4.2, 34];          // its corner (x, y)
rx   = [7, 30.5, 14.5];     // 433 MHz receiver, standing on its edge along the left wall
rx_at = [4.5, 1];
tx   = [19.5, 19.5, 8];     // 433 MHz transmitter, flat
tx_at = [14, 9];
tsop = [6.0, 5.6, 8.0];     // IR receiver, facing the front window
tsop_at = [14, -2];

module outline(inset = 0) offset(delta = -inset) polygon(opening);

// a pocket wall around a rectangle, with gaps where the wires go
module fence(at, size, h, gaps = []) {
    translate([at.x, at.y, plate]) difference() {
        translate([-fence_t, -fence_t, 0]) cube([size.x + 2 * fence_t, size.y + 2 * fence_t, h]);
        translate([0, 0, -1]) cube([size.x, size.y, h + 2]);
        for (g = gaps) translate(g[0]) cube(g[1]);
    }
}

module cradle() {
    difference() {
        linear_extrude(plate) outline(clr);
        for (b = bosses) translate([b.x, b.y, plate - magnet[1] + 0.01]) cylinder(d = magnet[0], h = magnet[1], $fn = 32);
        // the D1 mini's solder joints sink into the plate
        translate([d1_at.x + 1, d1_at.y + 1, plate - d1_bot - 0.1]) cube([d1.x - 2, d1.y - 2, d1_bot + 0.2]);
        // shallow dimples underneath for small rubber feet
        for (p = [[17, 4], [8, 24], [26, 24], [17, 70]]) translate([p.x, p.y, -0.01]) cylinder(d = 8.4, h = 0.6, $fn = 32);
        // the USB plug comes in at the back, low: the plate makes way (tech.py's tunnel)
        translate([17 - 12.5 / 2, 67, 6.0 - 8.5 / 2]) cube([12.5, 80, 8.5]);
        // a finger notch at the back, to pull the cradle out
        translate([17, 80, -1]) cylinder(d = 10, h = 1.6, $fn = 32);
    }
    // the pockets, never beyond the opening nor into the walls above it
    difference() {
        intersection() {
            linear_extrude(floor_z + 20) outline(clr + 0.6);
            union() {
            // D1 mini: a low fence, open at the USB end and on the sides for the wires
            fence(d1_at, [d1.x, d1.y], d1.z + 1, gaps = [[[-2, 8, 0.5], [d1.x + 4, 16, 9]], [[4, d1.y - 1, 0.5], [d1.x - 8, 4, 9]]]);
            // receiver: a slot it stands in
            fence(rx_at, [rx.x, rx.y], 5, gaps = [[[rx.x - 1, 6, 2], [4, 18, 9]]]);
            fence(tx_at, [tx.x, tx.y], 3, gaps = [[[-2, 4, 1], [4, 10, 9]]]);
            fence(tsop_at, [tsop.x, tsop.y], 4, gaps = [[[1, tsop.y - 1, 1], [tsop.x - 2, 4, 9]], [[1, -2, 1], [tsop.x - 2, 4, 9]]]);
            }
        }
        if (trim) import("out/body_hollow.stl");
    }
}

module parts() {
    color([0.1, 0.25, 0.6]) translate([d1_at.x, d1_at.y, plate]) cube(d1 + [0, 0, 3]);   // board + ESP can
    color([0.1, 0.5, 0.2]) translate([rx_at.x, rx_at.y, plate]) cube(rx);
    color([0.1, 0.5, 0.2]) translate([tx_at.x, tx_at.y, plate]) cube(tx);
    color([0.1, 0.1, 0.1]) translate([tsop_at.x, tsop_at.y, plate]) cube(tsop);
}

if (part == "cradle") cradle();
else if (part == "fit") { color([0.3, 0.55, 0.4]) cradle(); parts(); }
else if (part == "parts") parts();
