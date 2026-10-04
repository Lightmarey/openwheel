# SPDX-License-Identifier: MIT
"""Test oracle for the original HID capture; not imported by the frontend."""
import struct

def decode_report(data, report_ids=(1,10)):
    if len(data)!=4 or data[0] not in report_ids: return None
    # Report 1 is the standard Dial; report 10 uses the same layout on this firmware (captured on device).
    if data[0]==10 and data[1] not in (0,1): return None
    return bool(data[1]&1),struct.unpack_from('<h',data,2)[0]
