"""Synthetic import → actual installed ISCE metadata → MintPy load check.

This verifies file interoperability, not a real ALOS-2 scientific result.
"""
from pathlib import Path
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper.core import atomic_json
from alosstack_helper.legacy import create_wrapper, inspect_legacy, require_legacy_source
from alosstack_helper.workflows import inventory, options
from test_legacy import dataset

READY = all(importlib.util.find_spec(name) is not None for name in ('isce','mintpy','h5py','numpy'))


@unittest.skipUnless(READY, 'requires ISCE2 and MintPy scientific environment')
class LegacyScienceTests(unittest.TestCase):
    def test_sanitized_metadata_loads_actual_mintpy_without_modifying_original(self):
        import h5py
        import numpy as np
        from alosstack_helper.science_worker import load
        old = Path.cwd()
        with tempfile.TemporaryDirectory(prefix='legacy-science-') as tmp:
            p = Path(tmp)
            try:
                os.chdir(p)
                request, _ = dataset(p, Path(sys.prefix))
                source = Path(request['source'])
                hashes = lambda:{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in source.rglob('*') if f.is_file()}
                before = hashes(); state = create_wrapper(request, inspect_legacy(request), 'aabbccddeeff'); inv = inventory(state)
                dest = Path(state['config']['root'])/'analyses/001';dest.mkdir(parents=True)
                atomic_json(dest/'analysis.json',dict(id='001',kind='mintpy',status='running',history=[],inventory=inv,
                            options=options({},inv),environment=request['environment'],source_ion=state['ion'],source_ion_excluded=[]))
                with patch.dict(os.environ, {'MPLCONFIGDIR':str(p/'mpl'),'XDG_CACHE_HOME':str(p/'cache')}), contextlib.redirect_stdout(io.StringIO()):
                    load(dest/'analysis.json')
                require_legacy_source(state)
                self.assertEqual(before,hashes())
                with h5py.File(dest/'inputs/ifgramStack.h5') as f:
                    self.assertEqual(f['unwrapPhase'].shape,(3,16,32));np.testing.assert_allclose(f['coherence'][:],.9)
                    self.assertEqual(f['date'][:].astype(str).tolist(),[['20170826','20181103'],['20170826','20190518'],['20181103','20190518']])
                with h5py.File(dest/'inputs/geometryRadar.h5') as f:
                    np.testing.assert_allclose(f['latitude'][:],25);np.testing.assert_allclose(f['longitude'][:],121)
                    self.assertTrue(f['waterMask'][:].all())
                self.assertEqual(json.loads((dest/'loaded.json').read_text())['width'],32)
            finally: os.chdir(old)


if __name__=='__main__':unittest.main()
