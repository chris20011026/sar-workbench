"""An openly distributable synthetic MintPy lesson, not a satellite observation.

The fixture follows insar-arm's MIT-licensed numerical validation example.
Run the actual installed MintPy inversion and velocity estimator; compare with
known answers before publishing results to the lesson interface.
"""
import argparse
import datetime as dt
import itertools
import json
from pathlib import Path
import subprocess
import sys


def run(output):
    import h5py
    import numpy as np
    output.mkdir(parents=True, exist_ok=False)
    dates = [f'{year}{month:02d}01' for year in (2020, 2021) for month in (1, 4, 7, 10)]
    day = np.array([(dt.datetime.strptime(d, '%Y%m%d') - dt.datetime(2020, 1, 1)).days for d in dates])
    y, x = np.mgrid[:16, :20]
    # Positive toward the satellite; the top-left pixel is the zero reference.
    velocity = .0005 * x - .0008 * y
    expected = day[:, None, None] / 365.25 * velocity[None, :, :]
    pairs = [(i, j) for i, j in itertools.combinations(range(len(dates)), 2) if j - i <= 3]
    phase = np.asarray([-4 * np.pi / .236 * (expected[j] - expected[i]) for i, j in pairs], np.float32)
    with h5py.File(output / 'ifgramStack.h5', 'w') as f:
        f.create_dataset('date', data=np.array([[dates[i], dates[j]] for i, j in pairs], dtype='S8'))
        f.create_dataset('unwrapPhase', data=phase)
        f.create_dataset('coherence', data=np.ones_like(phase))
        f.create_dataset('connectComponent', data=np.ones_like(phase, dtype=np.int16))
        f.create_dataset('dropIfgram', data=np.ones(len(pairs), dtype=bool))
        f.create_dataset('bperp', data=np.zeros(len(pairs), dtype=np.float32))
        f.attrs.update(FILE_TYPE='ifgramStack', LENGTH='16', WIDTH='20', REF_Y='0', REF_X='0',
                       WAVELENGTH='0.236', ALOOKS='1', RLOOKS='1', UNIT='radian', PROCESSOR='isce')
    for args in [('ifgram_inversion', 'ifgramStack.h5', '-w', 'no'), ('timeseries2velocity', 'timeseries.h5')]:
        subprocess.run([sys.executable, '-m', 'mintpy.cli.' + args[0], *args[1:]],
                       cwd=output, check=True, timeout=240)
    with h5py.File(output / 'timeseries.h5') as f:
        observed = f['timeseries'][:]
    with h5py.File(output / 'velocity.h5') as f:
        actual = f['velocity'][:]
    np.testing.assert_allclose(observed, expected, atol=1e-6, rtol=1e-5)
    np.testing.assert_allclose(actual, velocity, atol=1e-5, rtol=2e-3)
    result = dict(synthetic=True, verified=True, dates=dates, pair_count=len(pairs),
                  width=20, height=16, velocity_mm_year=(actual * 1000).tolist(),
                  timeseries_mm=(observed * 1000).tolist(),
                  expected_mm_year=(velocity * 1000).tolist(),
                  max_error_mm=float(np.max(np.abs(observed - expected))) * 1000,
                  max_velocity_error_mm_year=float(np.max(np.abs(actual - velocity))) * 1000,
                  reference=dict(row=0, col=0, date=dates[0]),
                  meaning='合成教學資料：已知位移 → 合成干涉圖 → MintPy 反演。未執行原始 SAR 的 ISCE2 對位、干涉與解纏。')
    (output / 'result.json').write_text(json.dumps(result, allow_nan=False))
    print('WORKBENCH DEMO PASS: actual MintPy inversion agrees with the known synthetic displacement.')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    run(parser.parse_args().output.resolve())
