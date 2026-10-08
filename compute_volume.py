"""Bereken controleerbare volume-indicatoren; GEEN eigen V1-scoreformule."""
import csv, json, math, pathlib, re, statistics, datetime
BASE = pathlib.Path(__file__).resolve().parent
MANIFEST = BASE/'volume_manifest.json'
OUT = BASE/'AlphaPulsear_V1_Volume_COMPACT.csv'
AUDIT = BASE/'AlphaPulsear_V1_Volume_AUDIT.json'

def number(value):
    s = re.sub(r'\s|\u00a0|€', '', str(value)).strip()
    if ',' in s: s = s.replace('.', '').replace(',', '.')
    return float(s)

def date(value):
    s=str(value).strip()
    for fmt in ('%d-%m-%Y','%Y-%m-%d','%d/%m/%Y','%d/%m/%y'):
        try:return datetime.datetime.strptime(s,fmt).date()
        except ValueError:pass
    raise ValueError('Ongeldige datum '+s)

def load(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f,delimiter=';')
        fields=[x.strip() for x in (reader.fieldnames or [])]
        if not {'Datum','Sluiten','Aantal aandelen'} <= set(fields):
            raise ValueError('Vereiste kolommen ontbreken: '+repr(fields))
        records=[]
        for row in reader:
            d=date(row['Datum']); price=number(row['Sluiten']); vol=number(row['Aantal aandelen'])
            if not (math.isfinite(price) and price>0 and math.isfinite(vol) and vol>0):
                raise ValueError('Ongeldige koers/volume '+str(d))
            records.append((d,price,vol))
    records.sort()
    if len({r[0] for r in records})!=len(records):raise ValueError('Dubbele handelsdatums')
    if len(records)<147:raise ValueError('Te weinig dagen voor 20-daags gemiddelde en 126 referenties')
    return records

def indicators(records):
    result=[]
    for i in range(20,len(records)):
        prev=statistics.mean(r[2] for r in records[i-20:i]); vr=min(3.,records[i][2]/prev)
        delta=records[i][1]-records[i-1][1]
        signed=vr*(1 if delta>0 else (-1 if delta<0 else 0))
        result.append((records[i][0],records[i][2],prev,vr,signed))
    return result

def main():
    names=json.loads(MANIFEST.read_text(encoding='utf-8'))['files']
    if len(names)!=29 or len(set(names))!=29:raise ValueError('Manifest moet 29 unieke bestanden bevatten')
    rows=[];audit={};all_latest=[]
    for name in names:
        path=BASE/name
        try:
            if not path.exists():raise FileNotFoundError(name)
            rec=load(path); ind=indicators(rec); latest=ind[-1]
            hist=[r[4] for r in ind[-253:-1]]
            if len(hist)<126:raise ValueError('Minder dan 126 voorafgaande volume-indicatoren')
            # Percentiel van richtinggevoelige indicator, expliciet descriptief.
            percentile=100*sum(v<=latest[4] for v in hist)/len(hist)
            rows.append([name,rec[0][0].isoformat(),rec[-1][0].isoformat(),len(rec),
                         int(latest[1]),round(latest[2],3),round(latest[3],6),
                         round(latest[4],6),len(hist),round(percentile,4),'BASISDATA_GELDIG'])
            audit[name]={'status':'BASISDATA_GELDIG','days':len(rec),'latest':rec[-1][0].isoformat()}
            all_latest.append(rec[-1][0])
        except Exception as exc:
            audit[name]={'status':'DATA-ONVOLDOENDE','reason':str(exc)}
            rows.append([name,'','','','','','','','','','DATA-ONVOLDOENDE'])
    fields=['Bestand','Oudste_datum','Nieuwste_datum','Geldige_dagen','Dagvolume',
            'Gemiddelde_vorige_20','VR_max3','Richtinggevoelige_VR','Referentie_observaties',
            'Historisch_percentiel','Status']
    with OUT.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f,delimiter=';');w.writerow(fields);w.writerows(rows)
    audit['_meta']={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'files_ok':sum(x['status']=='BASISDATA_GELDIG' for x in audit.values()),
                    'files_total':len(names),'newest_common':min(all_latest).isoformat() if len(all_latest)==len(names) else None,
                    'score_status':'NIET_BEREKEND: vergrendelde V1-scoreomzetting en indexdekking niet gevalideerd',
                    'note':'Geen officiële Volume-score. Percentiel is descriptief op basis van signed VR.'}
    AUDIT.write_text(json.dumps(audit,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Geldige basisbestanden:',audit['_meta']['files_ok'],'/',len(names))
    if len(all_latest)!=len(names):raise SystemExit('Niet alle 29 bestanden geldig; zie AUDIT.json')
    # Laat oude beursdata niet doorgaan als actueel.
    if (datetime.date.today()-min(all_latest)).days>5:
        raise SystemExit('Historische gegevens zijn ouder dan 5 kalenderdagen; geen actuele update')
if __name__=='__main__':main()
