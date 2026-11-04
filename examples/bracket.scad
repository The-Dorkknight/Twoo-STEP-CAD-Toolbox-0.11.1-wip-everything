// Mounting bracket: a small example for SCAD2STEP.
// Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
// Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.

/* [Plate] */
// Plate length (mm)
length = 60; // [30:5:120]
// Plate width (mm)
width = 30; // [20:2:60]
// Plate thickness (mm)
thickness = 5; // [3:1:12]

/* [Holes] */
// Mounting hole diameter (mm)
hole_d = 5; // [3:0.5:10]
// Add a hexagonal nut trap under each hole
nut_trap = true;

/* [Rib] */
// Height of the stiffening rib (mm)
rib_h = 18; // [0:2:40]

$fn = 64;

module plate() {
    difference() {
        hull() for (x = [-1, 1], y = [-1, 1])
            translate([x * (length / 2 - 4), y * (width / 2 - 4), 0])
                cylinder(h = thickness, r = 4);
        for (x = [-1, 1])
            translate([x * (length / 2 - 10), 0, -1]) {
                cylinder(h = thickness + 2, d = hole_d);
                if (nut_trap) cylinder(h = 3, d = hole_d * 1.9, $fn = 6);
            }
    }
}

plate();
if (rib_h > 0)
    translate([0, 0, thickness - 0.01])
        rotate([90, 0, 0])
            linear_extrude(height = 4, center = true)
                polygon([[-length / 2 + 14, 0], [length / 2 - 14, 0], [length / 2 - 22, rib_h], [-length / 2 + 22, rib_h]]);
