-- Host-only mock: reconstruct SF02 bytes from the exact colored rectangles.
local source=assert(arg[1],'Pass ftp_console_color.lua')
-- Minimal storage API fixture; no game installation or real save is loaded.
local W={}
function W.valid(name)
    return type(name)=='string' and name:match('^[%w_.%-]+$') and not name:find('..',1,true)
end
function W.u32(n)
    return string.char(n%256,math.floor(n/256)%256,math.floor(n/65536)%256,math.floor(n/16777216)%256)
end
function W.adler(value)
    local a,b=1,0
    for i=1,#value do a=(a+value:byte(i))%65521;b=(b+a)%65521 end
    return b*65536+a
end
assert(W.adler('Wikipedia')==300286872,'Adler32 fixture check')
local files={['guodong.lua']='hello'}
local now,L,R,queued=0,false,false,nil
local S={filename='ftp_console_color.lua',buffer='SOURCE',saved='SOURCE',cursor=7,undo={},hex=false,slot=4,input={valid=true}}
function S.input:held(k) return k=='L' and L end
local palette={'0xff000000','0xff0000ff','0xff00ff00','0xff00ffff','0xffff0000','0xffff00ff','0xffffff00','0xffffffff'}
local indices={};for i,c in ipairs(palette) do indices[c]=i-1 end
local layers,cal,screens,maxrects,created,moved,hidden={}, {},0,0,0,0,0
local exited,handler=false,''
engine={now=function()return now end,isPush=function(_,k)return k==117 and R end,
    tag=function(_,t)assert(t[1]=='flip')end,setEventHandler=function(_,t)handler=t.onEnterFrame end}
editor={read=function(n)return files[n],files[n] and nil or 'Not found'end,
    write=function(n,v)assert(W.valid(n));files[n]=v;return true end,
    receive=function()S.receive={};return true end,openEditor=function()exited=true end,
    label=function()end}
function editor.screen(c)
    assert(c=='0xff000000');layers={};cal={};screens=screens+1
end
function editor.rect(id,x,y,w,h,color)
    local value=assert(indices[color],color)
    if type(id)=='string' then
        local i=assert(tonumber(id:match('^cal(%d)$')))
        assert(i>=0 and i<8 and x==160+i*120 and y==70 and w==100 and h==30 and value==i)
        assert(not cal[i]);cal[i]=true;return
    end
    assert(h==10 and w>0 and w%10==0 and value~=0)
    local layer=layers[id]
    if layer then
        assert(layer.w==w and layer.h==h and layer.value==value,'Reused rect cannot change size/color')
        moved=moved+1
    else
        layer={w=w,h=h,value=value};layers[id]=layer;created=created+1
    end
    layer.x=x;layer.y=y
    if x<0 or y<0 then assert(x+w<=0 and y+h<=0);hidden=hidden+1 end
end
seedsow_editor={storage=W,test_state=function()return S end}
function seedsow_editor_frame()
    assert(S.receive)
    if queued then
        S.receive.source=queued;S.receive.complete=true;S.filename='request';S.buffer=queued;S.saved='';S.cursor=0;S.hex=true;queued=nil
    end
end
local function frame(dt)now=now+(dt or 16);assert(ss_ftp_frame);ss_ftp_frame()end
local function u32(s,i)local a,b,c,d=s:byte(i,i+3);return a+b*256+c*65536+d*16777216 end
local function decode()
    for i=0,7 do assert(cal[i])end
    local cells,rects={},0
    for _,layer in pairs(layers) do
        local x,y,w,h=layer.x,layer.y,layer.w,layer.h
        if x>=0 and y>=0 then
            assert(x>=160 and x+w<=1120 and y>=120 and y+h<=600)
            assert(x%10==0 and y%10==0)
            local row=(y-120)/10;local col=(x-160)/10
            for i=col,col+w/10-1 do assert(not cells[row*96+i],'Visible rectangles overlap');cells[row*96+i]=layer.value end
            rects=rects+1
        end
    end
    maxrects=math.max(maxrects,rects)
    local out={};local value,bits=0,0
    for i=0,4607 do
        value=value*8+(cells[i] or 0);bits=bits+3
        if bits>=8 then
            bits=bits-8;local divisor=2^bits
            out[#out+1]=string.char(math.floor(value/divisor));value=value%divisor
        end
    end
    assert(bits==0 and value==0)
    local raw=table.concat(out);assert(#raw==1728 and raw:sub(1,4)=='SF02')
    local n,total,page,count,adler=u32(raw,5),u32(raw,9),u32(raw,13),u32(raw,17),u32(raw,21)
    assert(count==math.max(1,math.ceil(total/1664)) and page<count)
    local length=math.min(1664,total-page*1664);local payload=raw:sub(30,29+length)
    assert(u32(raw,26)==W.adler(raw:sub(1,25)..payload),'Page Adler mismatch')
    assert(raw:sub(30+length)==string.rep('\0',1728-29-length),'Nonzero tail')
    return {seq=n,total=total,page=page,count=count,adler=adler,status=raw:byte(25),payload=payload}
end
local function rearm()
    L=true;frame();L=false;frame();frame(349);assert(not S.receive);frame(1)
    local r=decode();assert(S.receive and r.seq==0 and r.status==0 and r.payload=='READY')
end
local function advance()
    R=false;frame();local before=screens;local prior=decode();R=true;frame()
    local current=decode();assert(current.page==(prior.page+1)%prior.count)
    assert(screens==before,'Page turns must reuse the screen')
    frame();assert(screens==before and decode().page==current.page,'Holding R must not skip a page');R=false;frame()
end
assert(loadfile(source))()
assert(handler=='ss_ftp_frame' and decode().payload=='READY')
local fixture={};for i=0,255 do fixture[#fixture+1]=string.char(i)end;fixture=table.concat(fixture)
local sizes={0,1,2,3,511,512,513,1663,1664,1665,65536}
for n,size in ipairs(sizes)do
    files['data.bin']=string.rep(fixture,math.ceil(size/256)):sub(1,size)
    if n>1 then rearm()end
    queued='G\n'..n..'\ndata.bin\n';frame()
    assert(not S.receive and S.buffer=='SOURCE' and S.saved=='SOURCE' and S.cursor==7 and not S.hex)
    local first=decode();local parts={}
    for page=0,first.count-1 do
        local r=decode();assert(r.seq==n and r.total==size and r.status==0 and r.page==page and r.adler==first.adler)
        parts[#parts+1]=r.payload;if page<first.count-1 then advance()end
    end
    local data=table.concat(parts);assert(data==files['data.bin'] and W.adler(data)==first.adler)
    if first.count>1 then advance();assert(decode().page==0 and decode().payload==parts[1]) end
end
rearm();queued='P\n99\nnew.bin\n\0\255\n';frame();local r=decode()
assert(r.seq==99 and r.status==0 and files['new.bin']=='\0\255\n')
rearm();queued='G\n100\n../x\n';frame();assert(decode().status==1)
L=true;frame();frame(1500);assert(exited and not ss_ftp_frame and handler=='seedsow_editor_frame')
assert(S.filename=='ftp_console_color.lua' and S.buffer=='SOURCE')
assert(moved>0 and hidden>0,'Pool reuse and hiding must both be exercised')
print('PASS: SF02 rect-to-byte reconstruction; palette/calibration, 11 boundary sizes through 64KiB, page+file Adler, zero padding, pool reuse/hiding/wraparound, R latch, GET/PUT, restore/exit; max data rects='..maxrects..'; created='..created..'; reused='..moved..'; hidden='..hidden)
