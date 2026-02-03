import { RouterInput, RoutingResult } from './types'
import { Grid } from './grid'
import { Pathfinder } from './pathfinder'
import { Router } from './router'
import { OutputGenerator } from './output'
import { Visualizer } from './visualizer'

export { RouterInput, RoutingResult } from './types'
export { Grid } from './grid'
export { Pathfinder } from './pathfinder'
export { Router } from './router'
export { OutputGenerator } from './output'
export { Visualizer } from './visualizer'

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
    router.getPads()
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

const exampleInput: RouterInput = {
  board: {
    boardWidth: 80,
    boardHeight: 200,
    gridResolution: 0.5
  },
  manufacturing: {
    traceWidth: 1.5,
    traceClearance: 2
  },
  footprints: {
    button: {
      pinSpacingX: 9.0,
      pinSpacingY: 6.0
    },
    controller: {
      pinSpacing: 2.5,
      rowSpacing: 10.0
    },
    battery: {
      padSpacing: 6.0
    },
    diode: {
      padSpacing: 5.0
    }
  },
  placement: {
    diodes: [
      { id: 'D1', x: 40, y: 5, signalNet: 'LED1_SIG' }
    ],
    batteries: [
      { id: 'BAT1', x: 40, y: 185 }
    ],
    buttons: [
      { id: 'BTN1', x: 24, y: 120, signalNet: 'BTN1_SIG' },
      { id: 'BTN2', x: 56, y: 120, signalNet: 'BTN2_SIG' },
      { id: 'BTN3', x: 24, y: 142, signalNet: 'BTN3_SIG' },
      { id: 'BTN4', x: 56, y: 142, signalNet: 'BTN4_SIG' }
    ],
    controllers: [
      {
        id: 'U1',
        x: 40,
        y: 60,
        pins: {
          'PC6': 'NC',
          'PD0': 'NC',
          'PD1': 'BTN1_SIG',
          'PD2': 'BTN2_SIG',
          'PD3': 'BTN3_SIG',
          'PD4': 'BTN4_SIG',
          'VCC': 'VCC',
          'GND1': 'GND',
          'PB6': 'NC',
          'PB7': 'NC',
          'PD5': 'LED1_SIG',
          'PD6': 'NC',
          'PD7': 'NC',
          'PB0': 'NC',
          'PB1': 'NC',
          'PB2': 'NC',
          'PB3': 'NC',
          'PB4': 'NC',
          'PB5': 'NC',
          'AVCC': 'VCC',
          'AREF': 'NC',
          'GND2': 'GND',
          'PC0': 'NC',
          'PC1': 'NC',
          'PC2': 'NC',
          'PC3': 'NC',
          'PC4': 'NC',
          'PC5': 'NC'
        }
      }
    ]
  }
}

async function main(): Promise<void> {
  console.log('PCB Router - Starting...')
  console.log('Input configuration:')
  console.log(JSON.stringify(exampleInput, null, 2))

  try {
    const result = await routeAndVisualize(exampleInput, './output/pcb')

    console.log('\nRouting Result:')
    console.log(`Success: ${result.success}`)
    console.log(`Traces routed: ${result.traces.length}`)

    if (result.failedNets.length > 0) {
      console.log('\nFailed nets:')
      for (const failed of result.failedNets) {
        console.log(`  - ${failed.netName}: ${failed.reason}`)
        console.log(`    Source: ${failed.sourcePin}`)
        console.log(`    Destination: ${failed.destinationPin}`)
      }
    }

    if (result.success) {
      console.log('\nOutput files generated:')
      console.log('  - output/pcb_debug.png (visualization)')
      console.log('  - output/pcb_positive.png (conductive mask)')
      console.log('  - output/pcb_negative.png (insulating mask)')
    }
  } catch (error) {
    console.error('Routing failed:', error)
    process.exit(1)
  }
}

if (require.main === module) {
  main()
}
