"""Pipeline stages — design, placer, router, scad, manufacturing.

Each stage reads the previous stage's artifact from the session and
writes its own.  The stages in order:

  design         — LLM agent selects components, nets, outline, UI placements
  placer         — position all components inside the outline
  router         — route conductive traces between pads
  scad           — generate OpenSCAD enclosure model
  manufacturing  — produce G-code + ink SVG for printing
"""
