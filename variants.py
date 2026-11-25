"""Selector-anchored approximate substrings. Never proof of a quotation."""
from __future__ import annotations

from collections import defaultdict
from resolver import norm, sha
from alignment import tokens_with_offsets, text_and_starts, OMISSION


def prefix_distance(query, source, budget):
    """Minimum token edit distance to any source prefix, including empty.
    Return the chosen length and whether equally good boundaries exist.
    """
    if not query:
        return 0, 0, False
    source = source[:len(query)+budget]
    prev = list(range(len(source)+1))
    for i, word in enumerate(query, 1):
        row = [i]
        for j, actual in enumerate(source, 1):
            row.append(min(prev[j]+1, row[-1]+1, prev[j-1]+(word!=actual)))
        prev = row
    cost = min(prev)
    ends = [i for i, value in enumerate(prev) if value==cost]
    end = min(ends, key=lambda n: (abs(n-len(query)), n))
    return cost, end, len(ends)>1


def source_variants(quote, selector, docs, max_hits=100, max_words=200):
    """Search bounded windows around an exact selector inside the source.
    Full quote comparison is token-based, tolerates insertions, deletions and
    substitutions. No paraphrase equivalence, no silent removal of omissions.
    """
    q, needle = norm(quote).split(), norm(selector).split()
    result = {'method':'selector_anchored_token_edit_v1', 'status':'no_candidate',
              'candidates':[], 'selector_hits_examined':0, 'truncated':False,
              'auto_publish':False, 'rights':'unreviewed'}
    if max_hits<1 or max_words<4:
        raise ValueError('invalid variant search limits')
    if len(q)<8 or len(q)>max_words or len(needle)<3:
        result['status']='unsupported_length'; return result
    positions = [i for i in range(len(q)-len(needle)+1) if q[i:i+len(needle)]==needle]
    if len(positions)!=1:
        result['status']='selector_not_unique_in_quote'; return result
    pos=positions[0]; end=pos+len(needle)
    budget=min(16,max(1,len(q)//4))
    rows=[]
    for doc in docs:
        text,_=text_and_starts(doc)
        tokens=tokens_with_offsets(text); words=[t[0] for t in tokens]
        for i, word in enumerate(words):
            if word!=needle[0] or words[i:i+len(needle)]!=needle: continue
            if result['selector_hits_examined']>=max_hits:
                result['truncated']=True; break
            result['selector_hits_examined']+=1
            j=i+len(needle)
            left=words[max(0,i-pos-budget):i][::-1]
            right=words[j:j+len(q)-end+budget]
            lc,ln,la=prefix_distance(q[:pos][::-1],left,budget)
            rc,rn,ra=prefix_distance(q[end:],right,budget)
            distance=lc+rc
            if distance>budget: continue
            lo,hi=tokens[i-ln][1],tokens[j+rn-1][2]
            passage=text[lo:hi]
            rows.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
                         'char_start':lo,'char_end':hi,'text':passage,
                         'normalized_sha256':sha(norm(passage).encode()),
                         'query_words':len(q),'source_words':j+rn-i+ln,
                         'token_edits':distance,'similarity':round(1-distance/max(len(q),j+rn-i+ln),4),
                         'boundary_ambiguous':la or ra,'boundary_verified':False,
                         'match':'omission_or_variant_review' if OMISSION.search(quote) else 'approximate_tokens_review',
                         'rights':'unreviewed'})
        if result['truncated']: break
    rows.sort(key=lambda r:(r['token_edits'],abs(r['source_words']-r['query_words']),r['url'],r['char_start']))
    result['candidates']=rows[:5]
    result['qualifying_hits']=len(rows)
    result['candidates_truncated']=len(rows)>5
    if result['truncated']: result['status']='search_budget_exceeded'
    elif rows: result['status']='variant_review_required'
    return result


def source_family(provider):
    # Conservative policy: the two archives share substantial upstream text.
    # This does not assert that every individual edition is identical.
    return 'mia_related_archives' if provider in ('mia','wikirouge') else provider


def group_evidence(rows):
    """Group normalized identical passages by case, method and source family.
    Preserve every original URL, rank and fingerprint. Never infer independent
    confirmation from mirrors or merge merely similar passages.
    """
    by_case=defaultdict(list)
    for row in rows: by_case[row['id']].append(row)
    report={}
    for case, case_rows in sorted(by_case.items()):
        families=sorted({source_family(r['provider']) for r in case_rows if r.get('source_status')})
        methods=defaultdict(dict)
        for row in case_rows:
            candidates={name:m.get('candidates',[]) for name,m in row.get('methods',{}).items()}
            candidates['source_exact']=row.get('fr_occurrences',[])
            candidates['source_variant']=row.get('source_diagnostics',{}).get('variant_search',{}).get('candidates',[])
            for method, items in candidates.items():
                for rank,c in enumerate(items,1):
                    text=norm(c.get('text',''))
                    if not text: continue
                    key=(source_family(row['provider']),sha(text.encode()))
                    group=methods[method].setdefault(key,{'source_family':key[0],'normalized_sha256':key[1],'references':[]})
                    group['references'].append({'provider':row['provider'],'url':c['url'],'rank':rank,'document_sha256':c.get('sha256'),'revision':c.get('revision'),'char_start':c.get('char_start'),'char_end':c.get('char_end'),'block':c.get('block'),'span':c.get('span')})
        report[case]={'source_families':families,'methods':{}}
        for method, groups in sorted(methods.items()):
            entries=list(groups.values())
            count=sum(len(g['references']) for g in entries)
            cross=sum(len({r['provider'] for r in g['references']})>1 for g in entries)
            report[case]['methods'][method]={'candidate_references':count,'distinct_normalized_passages':len(entries),
                'duplicate_references':count-len(entries),'cross_provider_duplicate_groups':cross,'groups':entries}
    return report


def primary_sufficient_for_review(row):
    """A candidate-routing heuristic, not editorial acceptance.
    Do not skip the fallback solely because lexical similarity is high.
    """
    return (row.get('provider')=='mia' and row.get('status')=='review_required'
            and row.get('scope_complete') is True
            and row.get('source_status')=='normalized_contiguous'
            and bool(row.get('methods',{}).get('alignment_sentence',{}).get('candidates')))
