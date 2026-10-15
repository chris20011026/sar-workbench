"""Draw a selected report point in the existing scientific runtime."""
import json
import sys
from datetime import datetime
from pathlib import Path


def checked_point(source, y, x):
    """Read one point only from self-contained, unchanged source containers."""
    import h5py
    if __package__:
        from .reports import source_path, source_identities
        from .science_worker import point_data
    else:
        # Direct invocation uses the selected scientific environment, while
        # the validation helpers belong to this exact workbench checkout.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from alosstack_helper.reports import source_path, source_identities
        from alosstack_helper.science_worker import point_data

    source=source_path(Path(source).parent,source)
    raw=source.read_bytes();job=json.loads(raw)
    identities=source_identities(source.parent,job)
    # science_worker resolves legacy relative timeseries paths from cwd.
    # Do not validate one file and accidentally sample a same-named file in
    # the server's working directory. Reports launch this worker in the run.
    if not Path(job['timeseries_file']).is_absolute() and Path.cwd().resolve()!=source.parent.resolve():
        raise ValueError('相對時間序列路徑必須從所選分析資料夾讀取。')
    for name in identities:
        with h5py.File(name,'r') as container:
            pending=[container];seen=set()
            while pending:
                group=pending.pop();identity=hash(group.id)
                if identity in seen:continue
                seen.add(identity)
                if len(seen)>10000:raise ValueError('HDF5 結構過大，無法完成報告來源檢查。')
                for key in group:
                    if not isinstance(group.get(key,getlink=True),h5py.HardLink):
                        raise ValueError('HDF5 含間接連結，請先建立獨立完整的分析成果再產生點位報告。')
                    item=group[key]
                    if isinstance(item,h5py.Group):pending.append(item)
                    elif isinstance(item,h5py.Dataset) and (item.is_virtual or item.external):
                        raise ValueError('HDF5 使用外部／虛擬資料集，無法以容器檔案身分驗證報告點位。')
    point=point_data(source,int(y),int(x))
    source_path(source.parent,source)
    if source.read_bytes()!=raw or source_identities(source.parent,job)!=identities:
        raise ValueError('抽取報告點位期間來源已改變，請重新產生。')
    return point


def draw(source, y, x, output, destination, comparison=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    point=checked_point(source,y,x)
    record={'point':point};series=[('Selected analysis',point,'#245c49')]
    if comparison:
        try:from .comparison_worker import point as compare_point
        except ImportError:from comparison_worker import point as compare_point
        manifest=json.loads(Path(comparison).read_text());side='left' if manifest['left']['id']==point['analysis_id'] else 'right'
        paired=compare_point(manifest,int(y),int(x),side);record['comparison_point']=paired;series=[]
        for key,color,name in [('left','#245c49','A'),('right','#a65b36','B')]:
            if paired.get(key,{}).get('available'):series.append((name,paired[key],color))
    Path(output).write_text(json.dumps(record,ensure_ascii=False,allow_nan=False))
    fig,ax=plt.subplots(figsize=(8,3.5),layout='constrained')
    for name,p,color in series:
        dates=[datetime.strptime(str(d), '%Y%m%d') for d in p['dates']]
        ax.plot(dates,p['displacement_mm'],marker='o',markersize=4,color=color,linewidth=1.5,label=name)
    if len(series)>1:ax.legend(loc='best')
    ax.axhline(0,color='#899b91',linewidth=.8);ax.grid(alpha=.18)
    locator=mdates.AutoDateLocator(minticks=3,maxticks=7)
    ax.xaxis.set_major_locator(locator);ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.set(xlabel='Acquisition date',ylabel='Relative LOS displacement (mm)',title=f"Pixel ({point['y']}, {point['x']})  |  {point['latitude']:.5f}, {point['longitude']:.5f}")
    fig.savefig(destination,dpi=180);plt.close(fig)


if __name__=='__main__':draw(*sys.argv[1:])
