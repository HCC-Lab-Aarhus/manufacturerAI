#!/usr/bin/env node
/**
 * CLI wrapper for PCB Router
 * 
 * Usage:
 *   echo '{"board": {...}, "placement": {...}}' | node dist/cli.js
 *   node dist/cli.js < input.json
 *   node dist/cli.js --input input.json --output ./output/pcb
 * 
 * Input: JSON RouterInput on stdin or via --input file
 * Output: JSON RoutingResult to stdout
 */

import * as fs from 'fs'
import * as path from 'path'
import { RouterInput, RoutingResult } from './types'
import { Router } from './router'
import { Visualizer } from './visualizer'
import { OutputGenerator } from './output'

interface CLIOptions {
  inputFile?: string
  outputPath?: string
  visualize: boolean
}

function parseArgs(): CLIOptions {
  const args = process.argv.slice(2)
  const options: CLIOptions = {
    visualize: false
  }

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--input' && args[i + 1]) {
      options.inputFile = args[++i]
    } else if (args[i] === '--output' && args[i + 1]) {
      options.outputPath = args[++i]
      options.visualize = true
    } else if (args[i] === '--visualize') {
      options.visualize = true
    }
  }

  return options
}

async function readInput(options: CLIOptions): Promise<RouterInput> {
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

  return JSON.parse(jsonStr) as RouterInput
}

async function main(): Promise<void> {
  const options = parseArgs()

  try {
    const input = await readInput(options)
    const router = new Router(input)
    const result = router.route()

    // Generate visualization if requested
    if (options.visualize && options.outputPath) {
      // Ensure output directory exists
      const outputDir = path.dirname(options.outputPath)
      if (outputDir && outputDir !== '.') {
        fs.mkdirSync(outputDir, { recursive: true })
      }
      
      const visualizer = new Visualizer(router.getGrid())
      await visualizer.saveToFile(
        `${options.outputPath}_debug.png`,
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
        await outputGen.saveToFiles(options.outputPath)
      }
    }

    // Output result as JSON to stdout
    console.log(JSON.stringify(result, null, 2))

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
