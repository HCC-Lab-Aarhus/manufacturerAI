# Important list

Stuff to get done before the deadline.

## Principal Component Analysis

Should just design the device "floating in space" not really tied to a specific orientation. We then do PCA to find the print orientation and PCB plane. Add z-bottom to vertices. Add supports.

## Jumpers

We need to add jumpers to the PCB. Some designs are just not possible to solve with planar routing. Optimize for least amount of jumpers. We can also optimize component placement to use resistors and other non-blocking components as jumpers. This should be a first priority, adding 0hm jumpers as a last resort.

## Fix side mounted components pins

The pins should be inset into the body when side mounted, as the orientation of the is rotated and the pins will therefore come out the component horizontally instead of vertically.

