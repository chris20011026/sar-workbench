"""Bounded, read-only radar-coordinate SLC amplitude previews.

Executed in the selected science environment, which supplies NumPy and Pillow.
Both physical complex64 SLCs and CEOS-backed virtual SLCs are described by an
explicit raw byte layout. No ISCE processing, calibration, geocoding, averaging,
or modification of the source image takes place.
"""
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile


MAX_SIZE = 768
SAMPLE_BYTES = 8
MAX_OFFSET = (1 << 63) - 1


def integer(value, label, minimum=0, maximum=MAX_OFFSET):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{label} 必須是 {minimum}–{maximum} 之間的整數。')
    return value


def source_stamp(st):
    return {
        'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns,
        'device': st.st_dev, 'inode': st.st_ino,
    }


def absolute_path(value, label):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(label + ' 必須是絕對路徑。')
    return Path(value)


def separate_output(value, source, label):
    output = absolute_path(value, label)
    if output.is_symlink() or output.resolve() == source.resolve():
        raise ValueError(label + ' 不可覆寫原始影像或使用符號連結。')
    if output.exists():
        if not output.is_file() or os.path.samefile(output, source):
            raise ValueError(label + ' 不可覆寫原始影像或非一般檔案。')
    if not output.parent.is_dir():
        raise ValueError(label + ' 的資料夾不存在。')
    return output


def layout(request):
    image = request.get('image')
    if not isinstance(image, dict):
        raise ValueError('缺少原始影像配置。')
    path = absolute_path(image.get('path'), '原始影像')
    width = integer(image.get('width'), '影像寬度', 1)
    length = integer(image.get('length'), '影像長度', 1)
    offset = integer(image.get('image_offset'), '影像起始位元組')
    stride = integer(image.get('line_offset'), '影像列間距', 1)
    byte_order = image.get('byte_order')
    if byte_order not in ('MSB', 'LSB'):
        raise ValueError('僅支援 MSB 或 LSB 的 complex64 影像。')
    if stride < width * SAMPLE_BYTES:
        raise ValueError('影像列間距小於一整列的 complex64 資料。')
    required = offset + (length - 1) * stride + width * SAMPLE_BYTES
    if required > MAX_OFFSET:
        raise ValueError('影像位元組範圍超出支援大小。')
    max_size = integer(request.get('max_size', MAX_SIZE), '預覽尺寸', 1, MAX_SIZE)
    stretch = request.get('stretch', {'low': 2, 'high': 98})
    if not isinstance(stretch, dict):
        raise ValueError('預覽百分位設定無效。')
    low, high = stretch.get('low'), stretch.get('high')
    if (isinstance(low, bool) or isinstance(high, bool)
            or not isinstance(low, (int, float)) or not isinstance(high, (int, float))
            or not math.isfinite(low) or not math.isfinite(high)
            or not 0 <= low < high <= 100):
        raise ValueError('預覽百分位必須滿足 0 ≤ low < high ≤ 100。')
    step = (max(width, length) + max_size - 1) // max_size
    return dict(path=path, width=width, length=length, image_offset=offset,
                line_offset=stride, required=required, byte_order=byte_order, step=step,
                out_width=(width + step - 1) // step, out_height=(length + step - 1) // step,
                low=float(low), high=float(high))


def read_samples(fd, info):
    """Read at most 768² complex samples, without loading intervening pixels.

    pread() is used instead of mmap so logical requested bytes are bounded.
    Filesystem/device page reads and caching may involve additional bytes.
    """
    import numpy as np

    dtype = np.dtype('>c8' if info['byte_order'] == 'MSB' else '<c8')
    data = np.empty((info['out_height'], info['out_width']), dtype=np.complex64)
    step, width = info['step'], info['out_width']
    read_calls = 0
    requested_bytes = 0

    def read_at(count, offset):
        nonlocal read_calls, requested_bytes
        read_calls += 1
        requested_bytes += count
        result = os.pread(fd, count, offset)
        if len(result) != count:
            raise ValueError('預覽讀取時影像不完整，可能已被移動或截斷。')
        return result

    for y in range(info['out_height']):
        start = info['image_offset'] + y * step * info['line_offset']
        if step == 1:
            row = read_at(width * SAMPLE_BYTES, start)
        else:
            row = b''.join(read_at(SAMPLE_BYTES, start + x * step * SAMPLE_BYTES)
                           for x in range(width))
        data[y] = np.frombuffer(row, dtype=dtype, count=width)
    return data, requested_bytes, read_calls


def render_amplitude(samples, low, high):
    import numpy as np

    finite = np.isfinite(samples.real) & np.isfinite(samples.imag)
    zero = finite & (samples.real == 0) & (samples.imag == 0)
    valid = finite & ~zero
    rgba = np.zeros((*samples.shape, 4), dtype=np.uint8)
    warnings = []
    vmin = vmax = None
    constant = False
    if valid.any():
        # Promote components before hypot so large finite float32 values do not
        # overflow and very small values do not underflow the logarithm.
        magnitude = np.hypot(samples.real[valid].astype(np.float64),
                             samples.imag[valid].astype(np.float64))
        db = 20.0 * np.log10(magnitude)
        vmin, vmax = map(float, np.percentile(db, [low, high]))
        constant = vmax - vmin <= max(1.0, abs(vmin), abs(vmax)) * 1e-12
        if constant:
            grey = np.full(db.shape, 128, dtype=np.uint8)
            warnings.append('取樣振幅的百分位範圍沒有可辨識的變化，以中灰色顯示有效像素。')
        else:
            grey = np.rint(np.clip((db - vmin) / (vmax - vmin), 0, 1) * 255).astype(np.uint8)
        rgba[valid, :3] = grey[:, None]
        rgba[valid, 3] = 255
    else:
        warnings.append('取樣位置沒有有限且非零的振幅，預覽為透明圖層。')
    if (~finite).any():
        warnings.append('NaN 或無限值的取樣像素已設為透明。')
    if zero.any():
        warnings.append('零振幅取樣像素已設為透明，未納入對數與百分位計算。')
    return rgba, dict(
        vmin=vmin, vmax=vmax, valid_fraction=float(valid.mean()),
        finite_fraction=float(finite.mean()), zeros_fraction=float(zero.mean()),
        invalid_fraction=float((~finite).mean()), constant=constant, warnings=warnings,
    )


def preview(request):
    from PIL import Image

    info = layout(request)
    source = info['path']
    output = separate_output(request.get('output'), source, '預覽輸出')
    if output.suffix.lower() != '.png':
        raise ValueError('預覽輸出必須是 PNG 檔案。')
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(source, flags)
    temporary = None
    try:
        before_stat = os.fstat(fd)
        if not stat.S_ISREG(before_stat.st_mode):
            raise ValueError('原始影像不是一般檔案。')
        before = source_stamp(before_stat)
        if before['size'] < info['required']:
            raise ValueError('影像大小不足，配置超出檔案範圍。')
        # Comparing both the descriptor and path also catches source replacement
        # while the already-open file descriptor still points to the old inode.
        if source_stamp(source.stat()) != before:
            raise ValueError('原始影像在開啟時已改變，請重新檢查。')
        samples, requested_bytes, read_calls = read_samples(fd, info)
        rgba, stats = render_amplitude(samples, info['low'], info['high'])
        with tempfile.NamedTemporaryFile(prefix='.slc-preview-', suffix='.png', dir=output.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
        Image.fromarray(rgba).save(temporary, format='PNG')
        if source_stamp(os.fstat(fd)) != before or source_stamp(source.stat()) != before:
            raise ValueError('原始影像在預覽期間已改變，本次預覽不予採用。')
        # Revalidate aliases immediately before atomic publication.
        separate_output(str(output), source, '預覽輸出')
        temporary.replace(output)
        temporary = None
        return dict(
            status='complete', output=str(output), width=info['out_width'], height=info['out_height'],
            source_width=info['width'], source_height=info['length'],
            range_step=info['step'], azimuth_step=info['step'],
            first_range_sample=0, first_azimuth_line=0,
            last_range_sample=(info['out_width'] - 1) * info['step'],
            last_azimuth_line=(info['out_height'] - 1) * info['step'],
            scale='20 log10 |SLC|', units='relative amplitude dB (uncalibrated)',
            stretch=dict(low=info['low'], high=info['high'], population='finite nonzero sampled amplitude'),
            sampling='regular point samples; no averaging',
            orientation=dict(coordinates='radar', horizontal='range sample index increases right',
                             vertical='azimuth line index increases down', geocoded=False),
            source_stamp=before, source_unchanged=True, sampled_bytes=requested_bytes,
            read_calls=read_calls, sample_count=int(samples.size),
            layout={k: info[k] for k in ('image_offset', 'line_offset', 'byte_order')},
            **stats,
        )
    finally:
        os.close(fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.slc-preview-',
                                         suffix='.json', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        raise SystemExit('Usage: python -m alosstack_helper.sampling_preview_worker request.json output.json')
    request_file = absolute_path(argv[0], '預覽請求')
    report = absolute_path(argv[1], '預覽報告')
    request = json.loads(request_file.read_text(encoding='utf-8'))
    # Validate all publication paths before reading pixels. An invalid report
    # target must not overwrite the source, request, or PNG even on failure.
    source = layout(request)['path']
    separate_output(str(report), source, '預覽報告')
    separate_output(str(report), request_file, '預覽報告')
    png = separate_output(request.get('output'), source, '預覽輸出')
    separate_output(str(png), request_file, '預覽輸出')
    if report.resolve() == png.resolve() or (report.exists() and png.exists() and os.path.samefile(report, png)):
        raise ValueError('預覽影像與報告必須使用不同檔案。')
    result = preview(request)
    separate_output(str(report), source, '預覽報告')
    write_json(report, result)


if __name__ == '__main__':
    main()
