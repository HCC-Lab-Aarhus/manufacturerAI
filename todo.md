# Immediate

DONE Fix tracing keep out zome. Should be increased to about the pin spacing in the mc.

DONE Add keep out zone to pins, same distance as traces. Basically any place we draw tracing, including pins even if there is no trace.

DONE The diode pins should be placed a further distance from the polygon edge.

DONE The ground trace blocks other traces. It goes straight for the edge when coming out of the MC, potentially blocking routing. It should have been ripped up by the outer traces if its in the way, but it didnt.

Done The placer is not working correctly. It should always create the most spacing possible between all components. It should optimize for the most equal gap around all components and polygon boundry. It should also prioritize placing the controller closer to the buttons than the battery.

DONE Router should try each combination of routing order for all nets. For each combination attempt, whenever it reaches a net which cannot be routed, it skips it and routes the remaining nets. When all are attempted once, it tries to route each trace which could not be routed with A* in a way that minimizes the amount of crossed traces already present. It should then rip up all those traces which it crosses, and try and route the remaining unrouted nets, including the ones it just ripped up, in a random order and again skipping the ones not routing without crossings. It then restarts the cycle of making a minimum crossing A* trace and rips up crossing traces, until it has attempted some 100 iterations of trying to route the skipped set. If it then breaks out of that loop, it skips to the next random combination.
Here it could be smart, for example if it breaks out of the inner rip up loop, it records the longest order combination which still could not be routed (should be the last iteration, 1000th?) and blocks any outer loop initial random order combination that starts with that order combination. it cannot just add it to the hash set because it could be any order combination in the remaining nets, or maybe we could add all combinations of the last nets order with the beginning combination, adding all potential following combinations to the map if thats clean. It seems like the best solution.

DONE Add rounded edges around the top. Should be parametric, so that the llm can decide how long and high the curve is. When cutting out the curve along the corners, it should just subtract on the top.

DONE Edge profile tool is behaving weirdly. When changed, the loading spinner is having long waits like it has stalled, and is not updating the model. Perhaps it triggers multiple renders when changed whie it is compiling. Should lock tool while compiling, and look into the other issues. it does eventually show the correct model, just opos through a lot before showing it. Should only trigger rerender and lock interaction when released.

DONE The agent should always mention the rounding parameters it chose, and the assigned pins it gets from the router when the routing succeeds. In the initial message, it should also both acknowledge the design parameters, and then say "with a curve..." so on.

The placer is still not placing the MC closest to the buttons. This should be the first priority, not just weighted higher.

The router is still sometimes placing routes too close. Specifically, sometimes a single cell. It also doenst always find a solution, even when there is one.

Use a faster 3d model generator to the web, only compile to stl after generating the faster view. Also only generate build plate stl, but generate others on demand.

The visualizations on the web are upside down. Both the layout, pcb debug and 3d model.

Increase button top hole diameter a few mm. They are also not showing in the model, but does show when sliced, fix that.

Tracing shell cutout should extend higher. As high as the other components go.
Add gap for hatch lip, so it fits into the bottom.

Find the notch on the spring on the end of the battery compartment cover, and move it further down towards the build plate. It should also be only half the height and width is is now.

Add slider for how many edges the llm should output in the edge polygon. Include number in system prompt.

Router should be able to auto select mc pins and button pins during rip ups. Should just report back which pins were assigned to what, which i think there already is some functionality for. Take a look in the pcb folder, which is a optimized but older version of the ts T* router in the manufacturerAI folder. Implement the optimizations from that, but apply it to our current implementation in a way that it integrates properly.

# Long term

Add "accessories" which adds on the outer shell polygon. Should create simple shapes, and decide a vertice it will be attatched to, and it will use the average normal between the two connected edges. User can move it around to connect on other vertices, perhaps changing the angle too, or maybe even storing two vertices to place it in the middle of an edge, or some percentage along the edge, and with the direction being the normal of that single edge.

Allow any type of device, not just remotes. A clicker could use a rf device, a dorbell should have a green LED when clicked, a radio should have a speaker, and so on. Use llm to reason about components required from a set list, and write custom code to the microcontroller from the dynamically routed tracings and device operational requirementes.