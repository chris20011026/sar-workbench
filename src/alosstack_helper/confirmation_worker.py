"""Resolve XML and call the selected ISCE2's pair builder without running processing."""
import json
import sys
from pathlib import Path


def resolve(xml, stack_path):
    import isce
    sys.path.insert(0, str(stack_path))
    from StackPulic import loadStackUserParameters
    from create_cmds import formPairs
    sys.argv = ['confirmation', str(xml)]
    stack = loadStackUserParameters(str(xml))

    def pairs(suffix=''):
        return formPairs(stack.dataDir, getattr(stack, 'numberOfSubsequentDates'+suffix),
                         *(getattr(stack, name+suffix) for name in (
                             'pairTimeSpanMinimum', 'pairTimeSpanMaximum', 'datesIncluded',
                             'pairsIncluded', 'datesExcluded', 'pairsExcluded')))

    return dict(pairs=pairs(), ion_pairs=pairs('Ion') if stack.doIon else [],
                resolved=dict(estimate=bool(stack.doIon), apply=bool(stack.applyIon),
                              subsequent=stack.numberOfSubsequentDates,
                              subsequent_ion=stack.numberOfSubsequentDatesIon,
                              polarization=stack.polarization))


if __name__ == '__main__':
    xml, stack_path, output = sys.argv[1:]
    result = resolve(xml, stack_path)
    Path(output).write_text(json.dumps(result))
