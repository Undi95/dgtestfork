"""Pocket Deck Lab — autonomous deck search against the real meta gauntlet.

Stages (resumable, each writes lab/results/<stage>.json):
  A  every attacker line in a standard shell          (screening, e2)
  B  pairs of the best lines (<= 2 energy types)        (screening, e2)
  C  genetic search over the whole card pool            (e2, noise-averaged elites)
  D  final validation of the top decks                  (e3, many games)

Usage:
  python3 lab/search/pipeline.py                 # full run
  python3 lab/search/pipeline.py --lock "B1a 42,B1a 62"   # build around given cards (stages C/D only, seeds from A/B containing them)
"""
import argparse, collections, hashlib, json, os, random, re, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BIN = os.path.join(ROOT, 'target/release/deckgym')
META = os.path.join(ROOT, 'lab/decks/meta')
RES = os.path.join(ROOT, 'lab/results')
os.makedirs(RES, exist_ok=True)
LOG = open(os.path.join(RES, 'pipeline.log'), 'a')

def log(*a):
    s = time.strftime('%H:%M:%S ') + ' '.join(str(x) for x in a)
    print(s, flush=True); LOG.write(s + '\n'); LOG.flush()

# Meta share (Limitless B4a, adjusted for B4b trends). Filenames in lab/decks/meta.
WEIGHTS = {'altaria': 7.7, 'lucario': 7.7, 'butterfree': 5.2, 'vespiquen': 4.5, 'suicune': 4.2, 'rayquaza': 3.1,
           'hydreigon': 3.1, 'hoopa': 2.9, 'blaziken': 2.9, 'magnezone': 2.7, 'altaria-greninja': 2.6, 'weezing': 2.5,
           'charizard': 2.4, 'dedenne': 4.0, 'manectric': 2.1, 'sceptile-greninja': 1.8, 'flygon': 1.5}

# ------------------------------------------------------------------ cards
DB = json.load(open(os.path.join(ROOT, 'database.json')))
CARD = {}
for e in DB:
    kind, c = next(iter(e.items())); c['kind'] = kind; CARD[c['id']] = c
def is_poke(c): return c['kind'] == 'Pokemon'
def is_basic(c): return (is_poke(c) and c['stage'] == 0) or (c['kind'] == 'Trainer' and c.get('trainer_card_type') == 'Fossil')
def sig(c):
    if is_poke(c):
        return ('P', c['name'], c['hp'], json.dumps(c['attacks'], sort_keys=True), json.dumps(c['ability'], sort_keys=True))
    return ('T', c['name'])
CANON = {}
for cid in sorted(CARD):
    s = sig(CARD[cid]); CANON.setdefault(s, cid)
UNIQUE = sorted(set(CANON.values()))
BY_NAME = collections.defaultdict(list)
for cid in UNIQUE: BY_NAME[CARD[cid]['name']].append(cid)
TRAINERS = [cid for cid in UNIQUE if CARD[cid]['kind'] == 'Trainer' and CARD[cid].get('trainer_card_type') != 'Fossil']
TRAINERS = list({CARD[c]['name']: c for c in TRAINERS}.values())
TID = {CARD[c]['name']: c for c in TRAINERS}
E = ['Grass', 'Fire', 'Water', 'Lightning', 'Psychic', 'Fighting', 'Darkness', 'Metal']

def atk_value(c):
    best = 0
    for a in c.get('attacks') or []:
        d = a.get('fixed_damage') or 0
        e = a.get('effect') or ''
        m = re.search(r'(\d+) more damage', e)
        if m: d += int(m.group(1)) // 2
        if re.search(r'Asleep|Paralyzed|Confused', e): d += 30
        best = max(best, d)
    return best

def pre_evo(c):
    if not c.get('evolves_from'): return None
    cands = [x for x in BY_NAME.get(c['evolves_from'], []) if is_poke(CARD[x])]
    return max(cands, key=lambda x: (atk_value(CARD[x]), CARD[x]['hp'])) if cands else None

def chain(cid):
    out = [cid]
    while True:
        p = pre_evo(CARD[out[0]])
        if p is None: break
        out.insert(0, p)
    return out if is_basic(CARD[out[0]]) else None

def energy_types(deck):
    cnt = collections.Counter()
    for cid in deck:
        c = CARD[cid]
        if not is_poke(c): continue
        for a in c.get('attacks') or []:
            for t in a['energy_required']:
                if t in E: cnt[t] += 1
        m = re.search(r'\[(\w)\] Energy from your Energy Zone', (c.get('ability') or {}).get('effect', '') or '')
        if m:
            L = {'G': 'Grass', 'R': 'Fire', 'W': 'Water', 'L': 'Lightning', 'P': 'Psychic', 'F': 'Fighting', 'D': 'Darkness', 'M': 'Metal'}
            if m.group(1) in L: cnt[L[m.group(1)]] += 3
    if not cnt:
        types = collections.Counter(CARD[c]['energy_type'] for c in deck if is_poke(CARD[c]) and CARD[c]['energy_type'] in E)
        return [types.most_common(1)[0][0]] if types else ['Psychic']
    return [t for t, _ in cnt.most_common(2)]

def valid(deck, lock=()):
    if len(deck) != 20: return False
    names = collections.Counter(CARD[c]['name'] for c in deck)
    if any(v > 2 for v in names.values()): return False
    if not any(is_basic(CARD[c]) for c in deck): return False
    present = set(names)
    for c in deck:
        ef = CARD[c].get('evolves_from')
        if ef and ef not in present: return False
    for c in lock:
        if c not in deck: return False
    # at most 2 energy types actually needed
    need = set()
    for c in deck:
        for a in CARD[c].get('attacks') or []:
            need |= {t for t in a['energy_required'] if t in E}
    return len(need) <= 2

def key(deck): return '|'.join(sorted(deck))

def write_deck(deck, path):
    lines = ['Energy: ' + ', '.join(energy_types(deck))]
    for cid, k in sorted(collections.Counter(deck).items()):
        lines.append(f"{k} {CARD[cid]['name']} {cid}")
    open(path, 'w').write('\n'.join(lines) + '\n')

def describe(deck):
    cnt = collections.Counter(deck)
    po = [f"{k} {CARD[c]['name']} ({c})" for c, k in sorted(cnt.items()) if is_poke(CARD[c])]
    tr = [f"{k} {CARD[c]['name']} ({c})" for c, k in sorted(cnt.items()) if not is_poke(CARD[c])]
    return po, tr

# ------------------------------------------------------------------ evaluation
CACHE_F = os.path.join(RES, 'cache.json')
CACHE = json.load(open(CACHE_F)) if os.path.exists(CACHE_F) else {}
_last_save = [time.time()]
def save_cache(force=False):
    if force or time.time() - _last_save[0] > 60:
        json.dump(CACHE, open(CACHE_F + '.tmp', 'w')); os.replace(CACHE_F + '.tmp', CACHE_F); _last_save[0] = time.time()

def run_games(deck, per_opp, player='e2', seed=None):
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f: path = f.name
    write_deck(deck, path)
    cmd = [BIN, 'simulate', path, META, '--num', str(per_opp * len(WEIGHTS)), '--players', f'{player},{player}', '-p', '-j', '2']
    if seed is not None: cmd += ['--seed', str(seed)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=ROOT)
        txt = out.stdout + out.stderr
    except subprocess.TimeoutExpired:
        txt = ''
    os.unlink(path)
    per = {}
    for blk in txt.split('Simulating against deck')[1:]:
        m = re.search(r': (\S+)\.txt', blk); w = re.search(r'Player 0 won: (\d+)', blk); d = re.search(r'Draws: (\d+)', blk)
        g = re.search(r'Running (\d+) games', blk)
        if m and w and g:
            n = int(g.group(1)); per[m.group(1)] = (int(w.group(1)) + (int(d.group(1)) if d else 0) / 2, n)
    return per

def evaluate(deck, per_opp, player='e2'):
    """Accumulates games in cache; returns (weighted winrate, total games)."""
    k = f'{player}:{key(deck)}'
    rec = CACHE.setdefault(k, {})
    per = run_games(deck, per_opp, player, seed=random.randrange(1 << 30))
    for opp, (w, n) in per.items():
        a = rec.get(opp, [0, 0]); rec[opp] = [a[0] + w, a[1] + n]
    save_cache()
    return score(deck, player)

def score(deck, player='e2'):
    rec = CACHE.get(f'{player}:{key(deck)}', {})
    tot = ws = n = 0
    for opp, w in WEIGHTS.items():
        if opp in rec and rec[opp][1]:
            tot += w * rec[opp][0] / rec[opp][1]; ws += w; n += rec[opp][1]
    return (tot / ws if ws else 0.0), n

# ------------------------------------------------------------------ deck builders
FILL = ["Professor's Research", "Professor's Research", 'Poké Ball', 'Poké Ball', 'Copycat', 'Cyrus', 'Sabrina',
        'Giant Cape', 'X Speed', 'Pokémon Center Lady', 'Red', 'Copycat', 'Giant Cape', 'Potion', 'Giovanni', 'Leaf',
        'Lucky Ice Pop', 'Lucky Ice Pop', 'Rocky Helmet', 'Iono']

def line_cards(ch, tight=False):
    if len(ch) == 1: return [ch[0]] * 2
    if len(ch) == 2: return [ch[0]] * 2 + [ch[1]] * 2
    return [ch[0]] * 2 + [ch[1]] * (1 if tight else 2) + [ch[2]] * 2

def shell(poke, has_s2):
    deck = list(poke)
    tr = (['Rare Candy', 'Rare Candy'] if has_s2 else []) + FILL
    for t in tr:
        if len(deck) >= 20: break
        cid = TID.get(t)
        if cid and collections.Counter(deck)[cid] < 2: deck.append(cid)
    return deck if len(deck) == 20 else None

def attacker_finals():
    out, seen = [], set()
    for cid in UNIQUE:
        c = CARD[cid]
        if not is_poke(c) or atk_value(c) < 50: continue
        s = (c['name'], json.dumps(c['attacks'], sort_keys=True))
        if s in seen: continue
        seen.add(s); out.append(cid)
    return out

# ------------------------------------------------------------------ stages
def stage_file(s): return os.path.join(RES, f'stage_{s}.json')

def stage_A(per_opp=4):
    if os.path.exists(stage_file('A')): return json.load(open(stage_file('A')))
    finals = attacker_finals(); log(f'A: {len(finals)} lignées')
    res = []
    for i, f in enumerate(finals):
        ch = chain(f)
        if not ch: continue
        d = shell(line_cards(ch), len(ch) == 3)
        if not d or not valid(d): continue
        s, n = evaluate(d, per_opp)
        res.append({'final': f, 'deck': d, 'score': s})
        if i % 50 == 0: log(f'A {i}/{len(finals)} best={max(r["score"] for r in res):.3f}')
    res.sort(key=lambda r: -r['score'])
    json.dump(res, open(stage_file('A'), 'w')); save_cache(True)
    return res

def stage_B(A, top=30, per_opp=6):
    if os.path.exists(stage_file('B')): return json.load(open(stage_file('B')))
    finals = [r['final'] for r in A[:top]]
    res = []
    for i in range(len(finals)):
        for j in range(i + 1, len(finals)):
            c1, c2 = chain(finals[i]), chain(finals[j])
            poke = line_cards(c1, True) + line_cards(c2, True)
            d = shell(poke, len(c1) == 3 or len(c2) == 3)
            if not d or not valid(d): continue
            s, n = evaluate(d, per_opp)
            res.append({'finals': [finals[i], finals[j]], 'deck': d, 'score': s})
        log(f'B {i+1}/{len(finals)} best={max([r["score"] for r in res] or [0]):.3f}')
    res.sort(key=lambda r: -r['score'])
    json.dump(res, open(stage_file('B'), 'w')); save_cache(True)
    return res

def poke_pool(deck):
    types = set(energy_types(deck)) | {'Colorless'}
    return [c for c in UNIQUE if is_poke(CARD[c]) and CARD[c]['energy_type'] in types]

def mutate(deck, rng, lock=()):
    for _ in range(200):
        d = list(deck)
        k = rng.choice([1, 1, 2, 2, 3])
        for _ in range(k):
            r = rng.random()
            idx = [i for i, c in enumerate(d) if c not in lock or d.count(c) > 1]
            i = rng.choice(idx)
            if r < 0.55:
                d[i] = rng.choice(TRAINERS)
            elif r < 0.8:
                d[i] = rng.choice(d)  # duplicate an existing card (count tweak)
            else:
                # bring in a whole small line from the deck's types
                f = rng.choice(poke_pool(d)); ch = chain(f) or [f]
                for c in ch[:2]:
                    j = rng.choice([x for x in range(20) if d[x] not in lock])
                    d[j] = c
        if valid(d, lock): return d
    return None

def stage_C(seeds, gens=30, pop=12, kids=4, per_opp=8, lock=(), tag='C'):
    f = stage_file(tag)
    state = json.load(open(f)) if os.path.exists(f) else {'gen': 0, 'pop': seeds[:pop]}
    rng = random.Random(1234 + state['gen'])
    P = [p for p in state['pop'] if valid(p, lock)]
    for g in range(state['gen'], gens):
        children = []
        for p in P:
            for _ in range(kids):
                c = mutate(p, rng, lock)
                if c: children.append(c)
        for c in children: evaluate(c, per_opp)
        for p in P: evaluate(p, per_opp // 2)  # elites accumulate games -> less noise
        allc = {key(x): x for x in P + children}.values()
        P = sorted(allc, key=lambda x: -score(x)[0])[:pop]
        b = P[0]; s, n = score(b)
        log(f'{tag} gen {g+1}/{gens} best={s:.3f} ({n} parties) | ' + ', '.join(f"{k}x{CARD[c]['name']}" for c, k in collections.Counter(b).most_common(6)))
        json.dump({'gen': g + 1, 'pop': P}, open(f, 'w')); save_cache(True)
    return P

def stage_D(P, top=5, per_opp=30, tag='D'):
    out = []
    for d in P[:top]:
        s, n = evaluate(d, per_opp, player='e3')
        rec = CACHE[f'e3:{key(d)}']
        out.append({'deck': d, 'score_e3': s, 'games_e3': n, 'score_e2': score(d)[0],
                    'per_opp': {o: rec[o][0] / rec[o][1] for o in rec}})
        log(f'{tag}: e3 {s:.3f} ({n} parties)')
    out.sort(key=lambda r: -r['score_e3'])
    # meta references with the same bot for comparison
    refs = {}
    for name in ('altaria', 'lucario', 'butterfree'):
        ref = parse_deck(os.path.join(META, name + '.txt'))
        s, n = evaluate(ref, per_opp // 2, player='e3'); refs[name] = s
    json.dump({'top': out, 'refs': refs}, open(stage_file(tag), 'w'))
    report(out, refs, tag)
    return out

def parse_deck(path):
    d = []
    for l in open(path):
        m = re.match(r'(\d+) .* ([A-Z][\w-]* \d+)\s*$', l.strip())
        if m: d += [CANON.get(sig(CARD[m.group(2)]), m.group(2))] * int(m.group(1))
    return d

def report(out, refs, tag):
    L = [f'# Résultats ({tag}) — {time.strftime("%Y-%m-%d %H:%M")}', '',
         'Taux de victoire pondéré contre les 17 listes du méta, bot expectiminimax profondeur 3.', '',
         '## Références (même bot)', ''] + [f'- {k} : {v*100:.1f} %' for k, v in refs.items()] + ['']
    for i, r in enumerate(out, 1):
        po, tr = describe(r['deck'])
        L += [f'## #{i} — {r["score_e3"]*100:.1f} % ({r["games_e3"]} parties e3, e2 : {r["score_e2"]*100:.1f} %)', '',
              'Énergie : ' + ', '.join(energy_types(r['deck'])), '', '**Pokémon**', ''] + [f'- {x}' for x in po] + \
             ['', '**Dresseurs**', ''] + [f'- {x}' for x in tr] + ['', '| Adversaire | Victoires |', '|---|---|'] + \
             [f'| {o} | {v*100:.0f} % |' for o, v in sorted(r['per_opp'].items(), key=lambda x: -WEIGHTS.get(x[0], 0))] + ['']
    open(os.path.join(RES, f'RESULTATS_{tag}.md'), 'w').write('\n'.join(L))
    log(f'rapport écrit: RESULTATS_{tag}.md')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--lock', default='', help='cartes imposées, ex "B1a 42,B1a 62"')
    ap.add_argument('--gens', type=int, default=30)
    a = ap.parse_args()
    lock = tuple(x.strip() for x in a.lock.split(',') if x.strip())
    t0 = time.time()
    A = stage_A(); log(f'A terminé ({(time.time()-t0)/60:.0f} min) top: ' + ', '.join(CARD[r['final']]['name'] for r in A[:8]))
    B = stage_B(A); log(f'B terminé ({(time.time()-t0)/60:.0f} min) top: ' + ' | '.join(' + '.join(CARD[f]['name'] for f in r['finals']) for r in B[:5]))
    seeds = [r['deck'] for r in (B[:8] + A[:4])]
    tag = 'C' if not lock else 'C_lock'
    if lock:
        seeds = [d for d in seeds if all(c in d for c in lock)] or []
        if not seeds:
            ch = [c for l in lock for c in (chain(l) or [l])]
            base = shell(list(dict.fromkeys(ch)) * 2, any(CARD[c].get('stage') == 2 for c in ch))
            seeds = [base] if base and valid(base, lock) else []
    P = stage_C(seeds, gens=a.gens, lock=lock, tag=tag)
    stage_D(P, tag='D' if not lock else 'D_lock')
    log(f'FINI en {(time.time()-t0)/3600:.1f} h')
