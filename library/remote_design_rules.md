# Remote design rules (Library)

## Defaults
- length: 180 mm
- width: 45 mm
- thickness: 18 mm
- wall: 1.6 mm
- corner radius: 6 mm
- buttons: 4x3 grid (12), diameter 9 mm, spacing 3 mm
- margins: top 20 mm, bottom 18 mm, side 6 mm
- hole clearance: 0.25 mm (radius expansion)

## Printability constraints
- min wall: 1.2 mm
- min button diameter: 5.0 mm
- min spacing: 1.0 mm

## Layout rules
- usable_width = width - 2*margin_side
- usable_length = length - margin_top - margin_bottom
- grid_width = cols*diam + (cols-1)*spacing
- grid_height = rows*diam + (rows-1)*spacing
- require: grid_width <= usable_width AND grid_height <= usable_length

## Auto-fix priority
1) Increase width/length
2) Decrease diameter
3) Decrease spacing
4) Otherwise reject with an actionable message
