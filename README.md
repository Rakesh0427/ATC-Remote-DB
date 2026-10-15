# ATC Remote DB

Small ground tool that turns a loadable ATC database image into an ACARS VHF injector file.

ACARS cannot carry the raw `.bin`. The tool hex-encodes it, cuts it into 220-character text blocks, and groups those blocks into ARINC 618 multiblock messages of at most 16. The aircraft side reassembles the hex, checks the CRCs, and only then writes EEPROM.

## Run the GUI

```bash
python gui.py
```

Open the printed URL. Choose the ATC `.bin`, leave the label at `C1`, and download the `.vhf` file. The page does not transmit. The ground injector sends the file.

`Load the SGC10001 sample` uses `samples/SGC10001.bin` (the uploaded `SGC10001 1.003` image, part `69001264-505`).

## Command line

```bash
python codec.py inspect "SGC10001 1.003"
python codec.py convert "SGC10001 1.003" SGC10001.vhf --label C1 --delay 1000
python codec.py decode SGC10001.vhf rebuilt.bin
```

## What one uplink line looks like

```
<SOH>?#ID#<ACK>C1A<STX>ATDB/U/C5D0/001/088/F/0000/<hex>/CRC4<ETB>
DELAY=1000
```

- `#ID#` is the injector's aircraft-address token. A real 7-character registration can be typed instead.
- `C1` is the uplink label. Do not use `RA`, `H1`, or `A1`–`A9`.
- `A` is the uplink block identifier. It restarts at `A` for each message.
- `<ETB>` continues a message. `<ETX>` ends it. A VHF message is at most 16 blocks.
- The first block is a header: file size and CRC32. Every later block is `F/<offset>/<hex>`.
- `DELAY=` is only for the lab injector. Use `1000`, not the `30000` from the pangram test file.

For `SGC10001` (8204 bytes) this is 88 data slices plus one header block: 89 air blocks in 6 messages. A one-centre patch would be one message. This tool sends the whole image.

## Test in VS Code

Open this folder in VS Code. Install the C/C++ extension if you want F5. gcc must be on PATH (`gcc --version`).

1. Terminal → Run Task → **Test ATC VHF sim**. This builds `c/test_sim` and runs the full proof on `samples/SGC10001.bin`. The terminal must end with `SIMULATION PASS`.
2. Terminal → Run Task → **Run ATC VHF menu** if you want to type 1–7 yourself. The terminal has to stay focused so the menu can read your keys.
3. Run and Debug → **Test ATC VHF (auto)** is the same proof under the debugger. **ATC VHF menu** is the interactive one. Both use the integrated terminal, because the menu reads stdin.

On Windows the tasks build `c/test_sim.exe`. Run them from the workspace root so `samples/SGC10001.bin` is found.

## C simulation

Menu program in `c/`. It reads the bin, writes a real ACARS `.vhf`, decodes that file, and compares size, CRC32, every byte, part number, centre count, the `EDYY` row, and the 24-byte trailer.

```bash
gcc -Wall -Wextra -std=c11 -o c/test_sim c/atc_codec.c c/test_sim.c
./c/test_sim
./c/test_sim --auto samples/SGC10001.bin
```

Menu: 1 read, 2 show database, 3 encode `.vhf`, 4 show uplink, 5 decode, 6 compare, 7 full proof, 8 settings, 0 exit.

## Tests

```bash
python -m unittest tests.test_codec
```
