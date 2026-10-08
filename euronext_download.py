import csv, datetime, pathlib, re, sys, time, os
from playwright.sync_api import sync_playwright

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / 'downloads'
HEADLESS = os.getenv('CI', '').lower() == 'true'
OUT.mkdir(exist_ok=True)
TODAY = datetime.date.today()
END = TODAY - datetime.timedelta(days=1)
START = END - datetime.timedelta(days=729)
STOCKS = 'AALBERTS|NL0000852564-XAMS\nABN AMRO|NL0011540547-XAMS\nADYEN|NL0012969182-XAMS\nAEGON|BMG0112X1056-XAMS\nAHOLD DELHAIZE|NL0011794037-XAMS\nAKZO NOBEL|NL0013267909-XAMS\nARCELORMITTAL|LU1598757687-XAMS\nASM INTERNATIONAL|NL0000334118-XAMS\nASML|NL0010273215-XAMS\nASR NEDERLAND|NL0011872643-XAMS\nBESI|NL0012866412-XAMS\nCVC CAPITAL|JE00BRX98089-XAMS\nDSM-FIRMENICH|CH1216478797-XAMS\nEXOR|NL0012059018-XAMS\nHEINEKEN|NL0000009165-XAMS\nIMCD|NL0010801007-XAMS\nING|NL0011821202-XAMS\nINPOST|LU2290522684-XAMS\nKPN|NL0000009082-XAMS\nMAGNUM ICE CREAM|NL0015002MS2-XAMS\nNN GROUP|NL0010773842-XAMS\nPHILIPS|NL0000009538-XAMS\nPROSUS|NL0013654783-XAMS\nRELX|GB00B2B0DG97-XAMS\nSBM OFFSHORE|NL0000360618-XAMS\nSHELL|GB00BP6MXD84-XAMS\nUMG|NL0015000IY2-XAMS\nUNILEVER|GB00BVZK7T90-XAMS\nWDP|BE0974349814-XBRU\nWOLTERS KLUWER|NL0000395903-XAMS'

def accept_cookies(page):
    for phrase in ('Reject All', 'Alles weigeren', 'Alles afwijzen', 'I Accept', 'Accept All'):
        try:
            el = page.get_by_role('button', name=re.compile('^'+re.escape(phrase)+'$', re.I)).first
            if el.is_visible(timeout=700):
                el.click(timeout=4000)
                page.wait_for_timeout(500)
                print('  Cookies:', phrase, flush=True)
                return
        except Exception: pass


def verify_dates(actual, expected):
    # Euronext sometimes clamps the first date by one day to its two-year limit.
    # Never silently accept other changes or a shifted end date.
    try:
        a0, a1 = (datetime.date.fromisoformat(v) for v in actual)
        e0, e1 = (datetime.date.fromisoformat(v) for v in expected)
    except (ValueError, TypeError):
        raise RuntimeError('Ongeldige datumwaarden: '+repr(actual))
    shift = (a0-e0).days
    if a1 != e1 or shift not in (0, 1):
        raise RuntimeError('Datums niet geaccepteerd: '+repr(actual))
    if shift == 1:
        print('  Euronext heeft begindatum 1 dag verschoven; geaccepteerd.',flush=True)

def set_dates(page):
    # Fixed IDs: the From field may become empty after datepicker validation.
    wanted = [('datetimepickerFrom', START.isoformat()),
              ('datetimepickerTo', END.isoformat())]
    for field_id, value in wanted:
        el = page.locator('#' + field_id)
        el.wait_for(state='visible', timeout=12000)
        print('  ', field_id, 'was', repr(el.input_value()), '->', value, flush=True)
        # Try normal user typing first; site datepicker often listens to blur/change.
        try:
            el.click(timeout=3000)
            el.fill(value, timeout=4000)
            el.press('Tab', timeout=3000)
            page.wait_for_timeout(1000)
        except Exception as exc:
            print('  Normaal invullen:', str(exc)[:120], flush=True)
        if el.input_value() != value:
            # Bootstrap/jQuery datepickers need their own setter, not just input.value.
            try:
                el.evaluate('''(e, v) => {
                    const $ = window.jQuery;
                    if ($) {
                        const q = $(e);
                        const date = new Date(v + 'T12:00:00');
                        if (q.data('DateTimePicker')) {
                            const picker = q.data('DateTimePicker');
                            if (window.moment) picker.date(window.moment(v, 'YYYY-MM-DD'));
                            else picker.date(date);
                        } else if (q.data('datepicker') || q.hasClass('hasDatepicker')) {
                            try { q.datepicker('setDate', date); } catch (_) {}
                        }
                    }
                    e.value = v;
                    e.dispatchEvent(new Event('input', {bubbles:true}));
                    e.dispatchEvent(new Event('change', {bubbles:true}));
                }''', value)
                page.wait_for_timeout(800)
            except Exception as exc:
                print('  Datepicker:', str(exc)[:120], flush=True)
        print('  ', field_id, 'nu', repr(el.input_value()), flush=True)
    actual = [page.locator('#'+k).input_value() for k,_ in wanted]
    expected = [v for _,v in wanted]
    print('  Datumvelden:', actual, flush=True)
    verify_dates(actual, expected)
    page.wait_for_timeout(1500)
    actual = [page.locator('#'+k).input_value() for k,_ in wanted]
    verify_dates(actual, expected)

def click_historical(page):
    # Product pages can use an embedded popup or a separate historical-data link.
    for loc in [page.get_by_text(re.compile('Historische prijs|Historical price|Historical data',re.I)).first,
                page.locator('a[href*="getHistoricalPrice"]').first]:
        try:
            if loc.is_visible(timeout=1800):
                loc.click(timeout=4000)
                page.wait_for_timeout(2000)
                return
        except Exception: pass

def download_file(page, deststem):
    """Use the historical-price export only; bypass Playwright click actionability stalls."""
    selectors = [
        'button.buttons-excel[aria-controls="AwlHistoricalPriceTable"]',
        'a[data-target="#awl_historical_price_dl"]',
        '#awlHistoricalPriceCanvas-download a',
    ]
    failures=[]
    for selector in selectors:
        loc=page.locator(selector).first
        try:
            if loc.count()==0: continue
            print('  Probeer historische export:',selector,flush=True)
            # A normal Playwright click can wait indefinitely on a moving/covered button.
            # A DOM click triggers the same page handler without that actionability wait.
            try:
                with page.expect_download(timeout=15000) as event:
                    loc.evaluate('(e) => e.click()')
                dl=event.value
            except Exception as exc:
                failures.append(selector+': '+str(exc).split('\n')[0][:90])
                # Some variants open a format-selection modal rather than downloading.
                modal=page.locator('#awl_historical_price_dl')
                if modal.count() and modal.is_visible():
                    try:
                        fmt=modal.locator('input[name="format"][value="xls"]')
                        if fmt.count(): fmt.check(force=True,timeout=3000)
                        with page.expect_download(timeout=15000) as event:
                            modal.locator('#submit_dl_form').evaluate('(e) => e.click()')
                        dl=event.value
                    except Exception as exc2:
                        failures.append('modal: '+str(exc2).split('\n')[0][:90])
                        continue
                else: continue
            ext=pathlib.Path(dl.suggested_filename).suffix.lower()
            if ext not in ('.xls','.xlsx','.csv'):
                raise RuntimeError('Onverwacht formaat: '+repr(dl.suggested_filename))
            dest=OUT/(deststem+ext)
            dl.save_as(dest)
            print('  Bestand ontvangen:',dest.name,dest.stat().st_size,'bytes',flush=True)
            return dest
        except Exception as exc:
            failures.append(selector+': '+str(exc).split('\n')[0][:90])
    raise RuntimeError('Historische export mislukt: '+'; '.join(failures[-4:]))

def validate_file(path, from_date, to_date):
    """Read XLS/XLSX/CSV, validate volume rows, date coverage and real history."""
    ext=path.suffix.lower()
    if ext == '.xlsx':
        from openpyxl import load_workbook
        book=load_workbook(path,read_only=True,data_only=True)
        try: rows=list(book.active.values)
        finally: book.close()
    elif ext == '.xls':
        import xlrd
        book=xlrd.open_workbook(str(path))
        sheet=book.sheet_by_index(0)
        rows=[[sheet.cell_value(r,c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
    elif ext == '.csv':
        with path.open('r',encoding='utf-8-sig',newline='') as f:
            sample=f.read(4096); f.seek(0)
            try: dialect=csv.Sniffer().sniff(sample,delimiters=';,\t')
            except csv.Error: dialect=csv.excel
            rows=list(csv.reader(f,dialect))
    else:
        raise RuntimeError('Onbekend bestandstype: '+ext)
    headers=None; headidx=None; datecol=None; volcol=None
    for i,row in enumerate(rows[:30]):
        labels=[str(v or '').strip().lower() for v in row]
        for c,label in enumerate(labels):
            if label in ('vol.','volume','number of shares','aantal aandelen','shares','number of shares traded') or 'number of shares' in label or 'aantal aandelen' in label:
                volcol=c;headidx=i;headers=labels;break
        if headidx is not None:
            for c,label in enumerate(labels):
                if label in ('date','datum') or label.startswith('date ') or label.startswith('datum '):datecol=c;break
            break
    if volcol is None or datecol is None:
        raise RuntimeError('Datum/volume-kolommen niet gevonden. Eerste regels: '+repr(rows[:3])[:150])
    dates=set(); positive=0; badvol=0
    for row in rows[headidx+1:]:
        if len(row)<=max(datecol,volcol):continue
        dv,vol=row[datecol],row[volcol]
        d=None
        if isinstance(dv,datetime.datetime):d=dv.date()
        elif isinstance(dv,datetime.date):d=dv
        elif isinstance(dv,(float,int)) and ext=='.xls':
            try:d=datetime.datetime(*xlrd.xldate_as_tuple(dv,book.datemode)).date()
            except Exception:pass
        elif isinstance(dv,str):
            for fmt in ('%d/%m/%Y','%Y-%m-%d','%d-%m-%Y','%m/%d/%Y'):
                try:d=datetime.datetime.strptime(dv.strip(),fmt).date();break
                except ValueError:pass
        if d is None or d in dates:continue
        dates.add(d)
        try:
            if isinstance(vol,str):
                vol=vol.strip().replace('\u00a0','').replace(' ','').replace('.', '') if re.fullmatch(r'[0-9. ]+',vol.strip()) else vol.strip().replace('\u00a0','').replace(' ','').replace(',','.')
            if float(vol)>0:positive+=1
            else:badvol+=1
        except (TypeError,ValueError):badvol+=1
    if not dates:raise RuntimeError('Geen geldige dagelijkse koersdatums gevonden')
    oldest,newest=min(dates),max(dates)
    print(f'  Inhoud: {len(dates)} unieke dagen, {positive} positieve volumes, {oldest} t/m {newest}',flush=True)
    if len(dates)<126 or positive<126:
        raise RuntimeError(f'ONVOLDOENDE HISTORIE: {len(dates)} dagen en {positive} geldige volumes (minimaal 126; voor V1-percentiel is ~252 historie plus 20-daags gemiddelde gewenst)')
    expected_from=datetime.date.fromisoformat(from_date)
    expected_to=datetime.date.fromisoformat(to_date)
    if oldest < expected_from-datetime.timedelta(days=7) or oldest > expected_from+datetime.timedelta(days=15):
        raise RuntimeError(f'Onverwachte oudste datum: {oldest} (gevraagd {expected_from})')
    if newest < expected_to-datetime.timedelta(days=10) or newest > expected_to+datetime.timedelta(days=1):
        raise RuntimeError(f'Onverwachte nieuwste datum: {newest} (gevraagd {expected_to})')
    return f'{len(dates)} handelsdagen, {positive} volume-observaties, {oldest} t/m {newest}'

def extract_full_table(page, stem, actual_from, actual_to):
    """Export all rows held in the client-side DataTables model, not the HTML viewport."""
    page.locator('table#AwlHistoricalPriceTable').first.wait_for(state='attached', timeout=20000)
    page.wait_for_timeout(1600)
    snap=page.evaluate(r'''() => {
      const $=window.jQuery;
      if (!$ || !$.fn.DataTable) return {error:'jQuery DataTables niet beschikbaar'};
      const tables=Array.from(document.querySelectorAll('table#AwlHistoricalPriceTable'));
      const active=tables.filter(t=>$.fn.DataTable.isDataTable(t));
      const table=active.find(t=>t.getClientRects().length>0) || active[0];
      if (!table) return {error:'Geen actieve historische DataTable',matches:tables.length};
      const dt=$(table).DataTable(), settings=dt.settings()[0], info=dt.page.info();
      const clean=(value)=>{
        if (value===null || value===undefined) return '';
        if (typeof value==='object') return JSON.stringify(value);
        const node=document.createElement('div');node.innerHTML=String(value);
        return (node.textContent||'').replace(/\s+/g,' ').trim();
      };
      const heads=Array.from(table.querySelectorAll('thead tr:last-child th')).map(h=>h.textContent.trim());
      // {search:'none'} and {order:'index'} include ALL cached rows, including
      // records currently absent from tbody due to pagination or display limits.
      const raw=dt.rows({search:'none',order:'index',page:'all'}).data().toArray();
      const columns=settings.aoColumns||[];
      const rows=raw.map((record,idx)=>{
        if (Array.isArray(record)) return record.map(clean);
        if (record && typeof record==='object') {
          return columns.map((c,i)=>{
            let value;
            if (typeof c.mData==='string') value=c.mData.split('.').reduce((v,k)=>v==null?undefined:v[k],record);
            else if (typeof c.mData==='number') value=record[c.mData];
            else value=record[i];
            if (value===undefined && typeof c.fnGetData==='function') {
              try {value=c.fnGetData(record,'display',{settings,row:idx,col:i});}catch(e){}
            }
            return clean(value);
          });
        }
        return [clean(record)];
      });
      return {matches:tables.length,active:active.length,serverSide:!!settings.oFeatures.bServerSide,
              total:info.recordsTotal,filtered:info.recordsDisplay,loaded:raw.length,
              headers:heads,rows:rows,example:rows.slice(0,2)};
    }''')
    print('  DataTables: ',{k:v for k,v in snap.items() if k not in ('rows','example','headers')},flush=True)
    print('  Kolommen:',snap.get('headers'),flush=True)
    print('  Voorbeeld:',snap.get('example'),flush=True)
    if snap.get('error'):raise RuntimeError(snap['error'])
    if snap.get('serverSide'):
        raise RuntimeError('Server-side tabel: records staan niet volledig in browsergeheugen')
    rows=snap.get('rows',[])
    if len(rows)<126:
        raise RuntimeError(f'DataTables heeft slechts {len(rows)} records in geheugen, verwacht minimaal 126')
    headers=snap.get('headers',[])
    if not headers or not any(('vol' in h.lower() or 'shares' in h.lower() or 'aantal aandelen' in h.lower()) for h in headers):
        raise RuntimeError('Volume-kolom niet herkenbaar: '+repr(headers))
    if any(len(row)!=len(headers) for row in rows):
        raise RuntimeError('Kolomaantallen komen niet overeen; export geweigerd')
    dest=OUT/(stem+'.csv')
    with dest.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f,delimiter=';');w.writerow(headers);w.writerows(rows)
    print('  Geheugendata opgeslagen:',dest.name,len(rows),'regels',flush=True)
    result=validate_file(dest,actual_from,actual_to)
    return dest,result


def run():
    print('AlphaPulsear Euronext cloud-update op basis van V14',flush=True)
    print('Periode:',START,'t/m',END,'(bij elke start bijgewerkt)',flush=True)
    print('V14 leest alle historische records en herkent Aantal aandelen als volume.',flush=True)
    pairs=[x.split('|') for x in STOCKS.strip().splitlines()]
    pairs.sort(key=lambda x:x[0]!='ASML')
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=HEADLESS, **({} if HEADLESS else {'channel':'chrome'}))
        context=browser.new_context(accept_downloads=True,locale='nl-NL',viewport={'width':1450,'height':960})
        for i,(name,code) in enumerate(pairs,1):
            page=context.new_page()
            print(f'[{i}/30] {name} ...',flush=True)
            result='NIET GELUKT';details=''
            stem=re.sub(r'[^A-Za-z0-9]+','_',name)
            try:
                page.goto('https://live.euronext.com/nl/product/equities/'+code.lower(),wait_until='domcontentloaded',timeout=60000)
                page.wait_for_timeout(3000)
                accept_cookies(page)
                click_historical(page)
                if len(context.pages)>1:page=context.pages[-1]
                set_dates(page)
                actual_from=page.locator('#datetimepickerFrom').input_value()
                actual_to=page.locator('#datetimepickerTo').input_value()
                dest,validation=extract_full_table(page,stem,actual_from,actual_to)
                result='GEVALIDEERD';details=f'{dest.name}; {validation}'
            except Exception as e:
                details=str(e).replace('\n',' ')[:350]
                try:page.screenshot(path=str(OUT/(stem+'_scherm.png')),timeout=4000)
                except Exception:pass
                try:(OUT/(stem+'_pagina.html')).write_text(page.content(),encoding='utf-8')
                except Exception:pass
            print(' ',result,details,flush=True)
            results.append([name,code,result,details])
            with (OUT/'STATUS.csv').open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f,delimiter=';');w.writerow(['Aandeel','Euronext code','Status','Details']);w.writerows(results)
            try:page.close()
            except Exception:pass
            if i==1 and result!='GEVALIDEERD':
                print('\nASML niet gevalideerd; de overige 29 worden niet gestart.',flush=True)
                break
            if i==1:print('\nASML gevalideerd. Nu de overige 29 aandelen.',flush=True)
            time.sleep(1)
        context.close();browser.close()
    print('\nKLAAR. Resultaten staan in de map downloads.',flush=True)

if __name__=='__main__':run()
