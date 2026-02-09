/**
 * PCB Router - Library re-exports
 *
 * All hardware constants (footprints, manufacturing, board params) are supplied
 * at runtime via the RouterInput JSON.  The Python bridge (ts_router_bridge.py)
 * reads them from configs/base_remote.json through hardware_config.py.
 *
 * Entry point for the production pipeline: cli.ts (stdin JSON → stdout JSON).
 */

export { RouterInput, RoutingResult } from './types'
export { Grid } from './grid'
export { Pathfinder } from './pathfinder'
export { Router } from './router'
export { OutputGenerator } from './output'
export { Visualizer } from './visualizer'

import { RouterInput, RoutingResult } from './types'
import { Router } from './router'
import { Visualizer } from './visualizer'
import { OutputGenerator } from './output'

export async function routePCB(input: RouterInput): Promise<RoutingResult> {
  const router = new Router(input)
  return router.route()
}

export async function routeAndVisualize(
  input: RouterInput,
  outputPath: string
): Promise<RoutingResult> {
  const router = new Router(input)
  const result = router.route()

  const visualizer = new Visualizer(router.getGrid())
  await visualizer.saveToFile(
    `${outputPath}_debug.png`,
    router.getTraces(),
    router.getPads(),
    {},
    router.getComponentBodies()
  )

  if (result.success) {
    const outputGen = new OutputGenerator(
      router.getGrid(),
      input.board,
      input.manufacturing,
      router.getTraces(),
      router.getPads()
    )
    await outputGen.saveToFiles(outputPath)
  }

  return result
}
