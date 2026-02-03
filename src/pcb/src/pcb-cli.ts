#!/usr/bin/env node
/**
 * PCB Router CLI - accepts pcb_layout.json format directly
 * 
 * Usage:
 *   node dist/pcb-cli.js --input pcb_layout.json --output ./output/
 *   cat pcb_layout.json | node dist/pcb-cli.js --output ./output/
 * 
 * Input: pcb_layout.json format from Python PCBAgent
 * Output: 
 *   - pcb_debug.png: Debug visualization
 *   - pcb_positive.png: Positive copper mask
 *   - pcb_negative.png: Negative copper mask  
 *   - routing_result.json: Routing result with traces
 */

import * as fs from 'fs'
import * as path from 'path'
import { RouterInput, RoutingResult, Button, Controller, Battery, Diode } from './types'
import { Router } from './router'
import { Visualizer } from './visualizer'
import { OutputGenerator } from './output'

// pcb_layout.json format from Python
interface PCBLayoutComponent {
  id: string
  type: 'button' | 'controller' | 'battery' | 'led'
  center: [number, number]
  footprint?: string
  pins?: Record<string, { net: string; position: [number, number] }>
  keepout?: { width_mm: number; height_mm: number }
  net?: string
}

interface PCBLayout {
  board: {
    outline_polygon: [number, number][]
    thickness_mm?: number
    origin?: string
  }
  components: PCBLayoutComponent[]
}

interface CLIOptions {
  inputFile?: string
  outputDir: string
}

function parseArgs(): CLIOptions {
  const args = process.argv.slice(2)
  const options: CLIOptions = {
    outputDir: '.'
  }

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--input' && args[i + 1]) {
      options.inputFile = args[++i]
    } else if (args[i] === '--output' && args[i + 1]) {
      options.outputDir = args[++i]
    }
  }

  return options
}

async function readInput(options: CLIOptions): Promise<PCBLayout> {
  let jsonStr: string

  if (options.inputFile) {
    jsonStr = fs.readFileSync(options.inputFile, 'utf-8')
  } else {
    // Read from stdin
    jsonStr = await new Promise<string>((resolve, reject) => {
      let data = ''
      process.stdin.setEncoding('utf-8')
      process.stdin.on('data', chunk => data += chunk)
      process.stdin.on('end', () => resolve(data))
      process.stdin.on('error', reject)
    })
  }

  return JSON.parse(jsonStr) as PCBLayout
}

function convertToRouterInput(layout: PCBLayout): RouterInput {
  // Extract board dimensions from outline polygon
  const xs = layout.board.outline_polygon.map(p => p[0])
  const ys = layout.board.outline_polygon.map(p => p[1])
  const boardWidth = Math.max(...xs) - Math.min(...xs)
  const boardHeight = Math.max(...ys) - Math.min(...ys)

  // Separate components by type
  const buttons: Button[] = []
  const controllers: Controller[] = []
  const batteries: Battery[] = []
  const diodes: Diode[] = []

  let buttonIndex = 0
  for (const comp of layout.components) {
    const [x, y] = comp.center

    switch (comp.type) {
      case 'button':
        buttonIndex++
        buttons.push({
          id: comp.id,
          x,
          y,
          signalNet: `${comp.id}_SIG`
        })
        break

      case 'controller':
        // Generate pin assignments based on button count
        const pins: Record<string, string> = {
          'VCC': 'VCC',
          'GND1': 'GND',
          'GND2': 'GND',
          'AVCC': 'VCC',
          'AREF': 'NC',
          'PC6': 'NC',
          'PB6': 'NC',
          'PB7': 'NC'
        }
        
        // Use pins from BOTH sides of the ATmega for better routing
        // Left side pins (lower numbers): PD0-PD7
        // Right side pins (higher numbers): PB0-PB5, PC0-PC5
        const leftPins = ['PD2', 'PD3', 'PD4', 'PD5', 'PD6', 'PD7']
        const rightPins = ['PB0', 'PB1', 'PB2', 'PB3', 'PB4', 'PB5']
        
        // Interleave pins from both sides for better routing
        let pinIdx = 0
        for (let i = 0; i < buttons.length && pinIdx < 12; i++) {
          const pin = i % 2 === 0 ? leftPins[Math.floor(i/2)] : rightPins[Math.floor(i/2)]
          if (pin) {
            pins[pin] = `SW${i + 1}_SIG`
          }
          pinIdx++
        }
        
        // Mark unused I/O pins as NC
        for (const pin of [...leftPins, ...rightPins, 'PD0', 'PD1', 'PC0', 'PC1', 'PC2', 'PC3', 'PC4', 'PC5']) {
          if (!(pin in pins)) {
            pins[pin] = 'NC'
          }
        }

        controllers.push({ id: comp.id, x, y, pins })
        break

      case 'battery':
        batteries.push({ id: comp.id, x, y })
        break

      case 'led':
        diodes.push({
          id: comp.id,
          x,
          y,
          signalNet: comp.net || `${comp.id}_SIG`
        })
        break
    }
  }

  return {
    board: {
      boardWidth,
      boardHeight,
      gridResolution: 0.5
    },
    manufacturing: {
      traceWidth: 1.2,      // mm - minimum for 3D printed PCB
      traceClearance: 1.5   // mm - minimum clearance
    },
    footprints: {
      button: { pinSpacingX: 9.0, pinSpacingY: 6.0 },
      controller: { pinSpacing: 2.54, rowSpacing: 7.62 },  // ATmega328P DIP-28
      battery: { padSpacing: 6.0 },
      diode: { padSpacing: 5.0 }
    },
    placement: {
      buttons,
      controllers,
      batteries,
      diodes
    }
  }
}

async function main(): Promise<void> {
  const options = parseArgs()

  try {
    const layout = await readInput(options)
    const routerInput = convertToRouterInput(layout)
    
    console.error(`Board: ${routerInput.board.boardWidth}x${routerInput.board.boardHeight}mm`)
    console.error(`Components: ${routerInput.placement.buttons.length} buttons, ${routerInput.placement.controllers.length} controllers`)
    
    const router = new Router(routerInput)
    const result = router.route()

    // Ensure output directory exists
    if (!fs.existsSync(options.outputDir)) {
      fs.mkdirSync(options.outputDir, { recursive: true })
    }

    // Generate debug visualization
    const visualizer = new Visualizer(router.getGrid())
    const debugImage = await visualizer.renderComplete(
      router.getTraces(),
      router.getPads()
    )
    fs.writeFileSync(path.join(options.outputDir, 'pcb_debug.png'), debugImage)
    console.error(`Generated: pcb_debug.png`)

    // Generate manufacturing outputs
    if (result.success || router.getTraces().length > 0) {
      const outputGen = new OutputGenerator(
        router.getGrid(),
        routerInput.board,
        routerInput.manufacturing,
        router.getTraces(),
        router.getPads()
      )
      await outputGen.saveToFiles(path.join(options.outputDir, 'pcb'))
      console.error(`Generated: pcb_positive.png, pcb_negative.png`)
    }

    // Save routing result
    const resultPath = path.join(options.outputDir, 'routing_result.json')
    fs.writeFileSync(resultPath, JSON.stringify(result, null, 2))
    console.error(`Generated: routing_result.json`)

    // Output result to stdout
    console.log(JSON.stringify(result, null, 2))

    if (!result.success) {
      console.error(`\nRouting completed with ${result.failedNets.length} failed nets`)
      process.exit(1)
    }

  } catch (error) {
    const errorResult: RoutingResult = {
      success: false,
      traces: [],
      failedNets: [{
        netName: 'SYSTEM',
        sourcePin: 'N/A',
        destinationPin: 'N/A',
        reason: error instanceof Error ? error.message : String(error)
      }]
    }
    console.log(JSON.stringify(errorResult, null, 2))
    process.exit(1)
  }
}

main()
