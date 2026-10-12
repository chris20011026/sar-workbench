"""Child-process isolation shared by processing, plotting and installation."""
import os
from pathlib import Path

PREFIXES = ('PYTHON', 'CONDA', 'MAMBA', 'DYLD_', 'LD_', 'ISCE', 'GDAL', 'PROJ',
            'MPL', 'FONTCONFIG', 'MAGICK_', '_INSAR_ARM_', '_CE_', 'CFLAGS', 'LDFLAGS')


def isolated_env(cache=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(PREFIXES) and k not in ('BASH_ENV', 'ENV', 'VIRTUAL_ENV')}
    # Fixed utility paths avoid an active environment's executable shadowing the
    # selected prefix. Legacy Homebrew rendering tools remain a fallback.
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin',
               PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               CONDARC='/dev/null', CONDA_REGISTER_ENVS='false',
               MPLBACKEND='Agg', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
    if cache is not None:
        env.update(XDG_CACHE_HOME=str(cache), MPLCONFIGDIR=str(Path(cache)/'matplotlib'))
    return env
