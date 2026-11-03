"""Render GitHub-safe SVG views of the checked Archify source graphs.

No browser, font download or third-party Python dependency is required.
Detailed relationship labels remain in SVG titles and the Archify JSON.
"""
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets/readme'
BG, FG, GREEN, AMBER, MUTED = '#f5f6f2', '#172820', '#397763', '#98713a', '#64746c'


def text(x, y, value, size=20, fill=FG, weight=400, anchor='start'):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>')


def lines(value, limit=24):
    """Wrap only at semantic separators or spaces; never shrink diagram type."""
    units = lambda s: sum(2 if ord(c)>255 else 1 for c in s)
    if units(value) <= limit:
        return [value]
    for separator in (' · ', ' / ', ' + '):
        if separator in value:
            parts = value.split(separator)
            if len(parts) == 2:
                return parts
    words = value.split()
    if len(words) > 1:
        middle = min(range(1,len(words)), key=lambda i:max(units(' '.join(words[:i])),units(' '.join(words[i:]))))
        return [' '.join(words[:middle]), ' '.join(words[middle:])]
    result, current = [], ''
    for char in value:
        if units(current+char)>limit:
            result.append(current)
            current=''
        current+=char
    return result+[current]


def box(n, x, y, w=200, h=106, color=GREEN):
    children = [f'<g id="{n["id"]}">', f'<title>{escape(n["label"] + ": " + n.get("sublabel", ""))}</title>',
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#ffffff" stroke="#c9d3cb"/>',
                f'<path d="M{x+18} {y+23}h18" stroke="{color}" stroke-width="3"/>',
                text(x+18,y+50,n['label'],22,FG,600)]
    for i,line in enumerate(lines(n.get('sublabel',''), 20 if w>=200 else 17)):
        children.append(text(x+18,y+73+20*i,line,18,MUTED))
    return '\n'.join(children+['</g>'])


def edge(e, path, label=None, color=GREEN):
    content = [f'<g id="{e["id"]}" data-from="{e["from"]}" data-to="{e["to"]}">',
               f'<title>{escape(e.get("label", ""))}</title>',
               f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" marker-end="url(#arrow-{color[1:]})"/>']
    if label:
        content.append(text(label[0],label[1],e['label'],18,color,400,label[2] if len(label)>2 else 'middle'))
    return '\n'.join(content+['</g>'])


def render(spec):
    zh = spec['meta']['locale']=='zh-CN'
    choose = lambda en, cn: cn if zh else en
    arch = spec['diagram_type']=='architecture'
    height = 810 if arch else 800
    title = choose('System architecture','系统架构') if arch else choose('From scene to results','从场景到结果')
    caption = choose('One desktop. Separate state. Local computation.','同一桌面窗口，独立工作区状态，本机计算。') if arch else choose('Two compute paths and one independent model viewer.','两条计算路径，以及独立的模型查看路径。')
    desc = '; '.join(f'{e["from"]} → {e["to"]}: {e.get("label", "")}' for e in spec.get('connections',spec.get('flows',[])))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">',
           f'<title id="title">RAN Workbench — {escape(title)}</title>',f'<desc id="desc">{escape(desc)}</desc>',
           '<defs>']
    for color in (GREEN,AMBER,MUTED):
        out.append(f'<marker id="arrow-{color[1:]}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L8 4L0 8Z" fill="{color}"/></marker>')
    out += ['</defs>',f'<rect width="1200" height="{height}" rx="18" fill="{BG}"/>',
            '<g font-family="Segoe UI, Microsoft YaHei, PingFang SC, sans-serif">',
            text(48,46,'01 / ARCHITECTURE' if arch else '02 / DATA FLOW',18,GREEN,600),
            text(48,96,title,40,FG,600),text(48,130,caption,21,MUTED)]
    if arch:
        coords = {'desktop':(48,365),'workbench':(350,165),'strategy':(350,365),'modeling':(350,580),
                  'runner':(660,265),'worker':(960,265),'cuda':(960,510),'meshes':(660,580)}
        routes = {
            'desktop-scene':('M248 395H292V218H350',None),
            'desktop-experiment':('M248 418H350',None),
            'desktop-viewer':('M248 442H292V633H350',None),
            'scene-copy':('M450 271V365',(462,324,'start')),
            'scene-job':('M550 218H605V295H660',(599,202)),
            'experiment-job':('M550 418H605V342H660',(601,446)),
            'spawn-worker':('M860 298H960',(910,284)),
            'worker-events':('M960 343H860',(910,390)),
            'rt-only':('M1060 371V510',(1048,450,'end')),
            'viewer-reads':('M550 633H660',(605,617)),
        }
        for e in spec['connections']:
            p,l=routes[e['id']]
            out.append(edge(e,p,l,MUTED if e['id'] in ('desktop-viewer','viewer-reads') else GREEN))
        for n in spec['components']:
            color=MUTED if n['id'] in ('modeling','meshes') else GREEN
            out.append(box(n,*coords[n['id']],color=color))
        out += [text(48,730,choose('RT requires CUDA. Synthetic Strategy mode is explicitly selected.','RT 模式需要 CUDA；策略合成演示需显式选择。'),21,FG),
                text(48,762,choose('Modeling meshes are never passed to the propagation solver.','Modeling 精细网格不输入传播求解器。'),20,MUTED)]
    else:
        xpos=[48,278,508,738,968]
        ypos={0:228,2:442,4:656}
        lane_colors={0:GREEN,2:AMBER,4:MUTED}
        coords={n['id']:(xpos[n['stage']],ypos[n['row']]) for n in spec['nodes']}
        for i,stage in enumerate(spec['stages']):
            out.append(text(xpos[i]+12,177,f'{i+1:02d}  {stage["label"]}',18,MUTED,600))
        out += [text(48,215,choose('PROPAGATION · geometry to radio coverage','传播 · 从几何到无线覆盖'),20,GREEN,600),
                text(48,429,choose('STRATEGY · NR slots','策略 · NR 时隙实验'),20,AMBER,600),
                text(48,643,choose('MODELING · user-supplied assets, view only','建模 · 用户自备素材，仅供查看'),20,MUTED,600)]
        nodes={n['id']:n for n in spec['nodes']}
        for e in spec['flows']:
            x,y=coords[e['from']];tx,ty=coords[e['to']]
            if e['id']=='copy-scene':
                out.append(edge(e,f'M{x+94} {y+110}V{ty}',(x+108,y+166,'start'),AMBER))
            else:
                color=lane_colors[nodes[e['from']]['row']]
                out.append(edge(e,f'M{x+184} {y+55}H{tx}',color=color))
        for n in spec['nodes']:
            out.append(box(n,*coords[n['id']],w=184,h=110,color=lane_colors[n['row']]))
        out += [text(758,700,choose('Scene / experiment hashes','场景 / 实验指纹'),22,FG,600),
                text(758,730,choose('checked before accepting results.','接收结果前核对版本。'),20,MUTED),
                text(758,762,choose('Export is a separate user action.','导出由用户另行触发。'),20,MUTED)]
    out += ['</g>','</svg>']
    return '\n'.join(out)+'\n'


def main():
    check='--check' in sys.argv
    for kind in ('architecture','dataflow'):
        for locale in ('en','zh'):
            name=f'{kind}-{locale}'
            spec=json.loads((ASSETS/f'{name}.archify.json').read_text(encoding='utf-8'))
            content=render(spec)
            target=ASSETS/f'{name}.svg'
            if check:
                if not target.exists() or target.read_text(encoding='utf-8') != content:
                    raise SystemExit(f'Stale diagram: {target.relative_to(ROOT)}')
            else:
                target.write_text(content,encoding='utf-8',newline='\n')
            print(f'{"Checked" if check else "Rendered"} {target.relative_to(ROOT)}')


if __name__=='__main__':
    main()
