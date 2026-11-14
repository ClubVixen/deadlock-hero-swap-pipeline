"""Jiggle selection and tuning without Blender dependencies."""
import fnmatch

def source_name(name):
    return name[3:] if name.startswith('CV_') else name

def matches(name, patterns):
    return any(fnmatch.fnmatchcase(candidate.lower(), p.lower())
               for candidate in (name, source_name(name)) for p in patterns)

def split_patterns(text):
    return [p.strip() for p in text.split(',') if p.strip()]

def select(names, config, include='', exclude=''):
    patterns = config.get('keep_patterns', []) + split_patterns(include)
    excluded = split_patterns(exclude)
    return sorted(n for n in names if matches(n, patterns) and not matches(n, excluded))

def rules(names, config, stiffness=1., damping=1., angle=1.):
    out=[]
    for name in names:
        rule=next((p for p in config['presets'] if matches(name,[p['pattern']])),None)
        if not rule or rule.get('skip'):continue
        fields=dict(rule['fields'])
        for k,v in list(fields.items()):
            if 'stiffness' in k:fields[k]=v*stiffness
            elif 'damping' in k:fields[k]=v*damping
            elif k in ('angle_limit','min_yaw','max_yaw','min_pitch','max_pitch'):
                fields[k]=max(-180.,min(180.,v*angle))
        out.append({'pattern':name,'fields':fields})
    return out
