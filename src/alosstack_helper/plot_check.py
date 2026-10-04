"""Exercise rendering, including binary loading, fonts, and image conversion."""
from pathlib import Path
import struct
import subprocess
import tempfile


def check_plotting():
    with tempfile.TemporaryDirectory(prefix='alos-plot-check-') as tmp:
        folder = Path(tmp)
        # Use short relative names: legacy mdx has limited filename lengths.
        (folder/'phase').write_bytes(struct.pack('256f', *[i/256 for i in range(256)]))
        commands = [
            ['mdx','phase','-s','16','-r4','-cmap','cmy','-wrap','6.283185307179586','-P','-workdir','.'],
            ['montage','-font','DejaVu-Sans','-label','test','out.ppm','-geometry','+1','check.tif'],
            ['convert','check.tif','check.png'],
        ]
        for command, output in zip(commands, ['out.ppm','check.tif','check.png']):
            result = subprocess.run(command, cwd=folder, capture_output=True, text=True, timeout=60)
            p = folder/output
            if result.returncode or not p.is_file() or not p.stat().st_size:
                raise RuntimeError(f'繪圖工具 {command[0]} 無法實際產圖，請檢查函式庫架構、字型與安裝：\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}')
        if not (folder/'check.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
            raise RuntimeError('繪圖測試未產生有效 PNG。')
    print('繪圖檢查通過：mdx、montage 字型與 PNG 轉換。')


if __name__ == '__main__':
    check_plotting()
