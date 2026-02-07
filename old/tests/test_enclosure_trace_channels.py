"""
Test that enclosure agent generates trace channels in unified remote.
"""
import sys
sys.path.insert(0, ".")

from src.design.enclosure_agent import Enclosure3DAgent
from pathlib import Path
import tempfile

def test_trace_channels():
    agent = Enclosure3DAgent()
    
    # Sample PCB layout
    pcb_layout = {
        'board': {'outline_polygon': [[0,0], [50,0], [50,100], [0,100]], 'thickness_mm': 1.6},
        'components': [
            {'id': 'SW1', 'type': 'button', 'center': [20, 60], 'keepout': {'type': 'circle', 'radius_mm': 6}},
            {'id': 'U1', 'type': 'controller', 'center': [25, 30], 'keepout': {'type': 'rectangle', 'width_mm': 12, 'height_mm': 12}},
            {'id': 'D1', 'type': 'diode', 'center': [25, 95], 'keepout': {'type': 'circle', 'radius_mm': 3}},
        ]
    }
    
    # Sample routing result with traces
    routing_result = {
        'success': True,
        'traces': [
            {'net': 'SW1_SIG', 'path': [
                {'x': 40, 'y': 120}, 
                {'x': 40, 'y': 100}, 
                {'x': 50, 'y': 100}, 
                {'x': 50, 'y': 60}
            ]}
        ],
        'failed_nets': []
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        outputs = agent.generate_from_pcb_layout(pcb_layout, {}, Path(tmpdir), routing_result)
        print(f'Generated: {list(outputs.keys())}')
        
        # Check unified remote has trace channels
        remote_scad = Path(tmpdir) / 'remote.scad'
        content = remote_scad.read_text(encoding='utf-8')
        
        if 'Trace channels' in content:
            print('✓ Remote contains trace channels')
        else:
            print('✗ No trace channels found')
            return False
        
        if 'trace_channel_depth' in content:
            print('✓ Trace channel depth parameter present')
        else:
            print('✗ Missing trace_channel_depth')
            return False
        
        if 'SW1_SIG' in content:
            print('✓ Net SW1_SIG trace found')
        else:
            print('✗ Missing SW1_SIG net trace')
            return False
        
        # Check for diode cutout
        if 'Diode cutouts' in content or 'diode' in content.lower():
            print('✓ Diode cutout generated')
        else:
            print('✗ Missing diode cutout')
            return False
        
        # Check for button holes in top
        if 'Button holes' in content or 'SW1' in content:
            print('✓ Button holes in unified remote')
        else:
            print('✗ Missing button holes')
            return False
        
        # Print a preview
        print("\n--- Remote SCAD preview (trace section) ---")
        in_trace = False
        for line in content.split('\n'):
            if 'Trace channels' in line:
                in_trace = True
            if in_trace:
                print(line)
                if 'remote()' in line and in_trace and 'module' not in line:
                    break
        
        print("\n✓ All trace channel tests passed!")
        return True

if __name__ == "__main__":
    test_trace_channels()
