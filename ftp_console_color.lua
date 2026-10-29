-- Eight-color optical download bridge for the current game's save files.
-- Requests: op LF decimal-sequence LF filename LF binary-data (L/G/P).
-- SF02: original 25-byte header, page Adler32, 1664-byte payload, zero pad.
-- Grid symbols: 3 bits each, MSB first; RGB corner palette and per-frame calibration.
local p,e=editor,engine
local E=assert(seedsow_editor,'Editor unavailable')
local S,W=E.test_state(),E.storage
local original=assert(seedsow_editor_frame,'Editor frame unavailable')
assert(S.input.valid,'Calibrate controller input first')
local MAX,INDEX,CHUNK=65536,'ftp-index.txt',1664
local fields={'filename','buffer','saved','cursor','undo','hex','hex_pending','slot','output'}
local saved={}
for _,k in ipairs(fields) do saved[k]=S[k] end
local function restore()
    for _,k in ipairs(fields) do S[k]=saved[k] end
    S.running=false; S.user_update=nil; S.queue={}
end
local function valid(n) return W.valid(n) and #n<=64 end
local function push(k) local v=e:isPush(k); return v==true or v==1 end
local names,seen={},{}
local function add(n)
    if valid(n) and n~=INDEX and not seen[n] then
        seen[n]=true; names[#names+1]=n; return true
    end
end
add('guodong.lua')
for n in (p.read(INDEX) or ''):gmatch('[^\r\n]+') do add(n) end
local function save_index() p.write(INDEX,table.concat(names,'\n')..'\n') end
local mode,seq,data,status,page,count,checksum='show',0,'READY',0,0,1,0
local ltime,arm,rheld
local colors={'0xff000000','0xff0000ff','0xff00ff00','0xff00ffff',
    '0xffff0000','0xffff00ff','0xffffff00','0xffffffff'}
local shifts={2097152,262144,32768,4096,512,64,8,1}
local pools,nextid={},0
local function show(reset)
    local head='SF02'..W.u32(seq)..W.u32(#data)..W.u32(page)..W.u32(count)..W.u32(checksum)..string.char(status)
    local payload=data:sub(page*CHUNK+1,(page+1)*CHUNK)
    local raw=head..W.u32(W.adler(head..payload))..payload
    raw=raw..string.rep('\0',1728-#raw)
    if reset then
        p.screen('0xff000000'); pools={}; nextid=0
        for i=0,7 do p.rect('cal'..i,160+i*120,70,100,30,colors[i+1]) end
    end
    p.label('ftp',160,24,22,'FTP COLOR '..seq..' / '..(page+1)..'/'..count..' / '..#data..' bytes / '..(status==0 and 'OK' or 'ERROR'))
    p.label('keys',160,640,18,'R: next page   L: next request   Hold L: editor')
    local used={}
    for y=0,47 do
        local run,color,block=0,nil,0
        for x=0,96 do
            local symbol
            if x<96 then
                local offset=x%8
                if offset==0 then
                    local at=y*36+math.floor(x/8)*3+1
                    local a,b,c=raw:byte(at,at+2)
                    block=a*65536+b*256+c
                end
                symbol=math.floor(block/shifts[offset+1])%8
            end
            if symbol~=color then
                if color and color~=0 then
                    -- editor.rect fixes size/color at creation, so only reuse
                    -- layers from the same color and run-width bucket.
                    local key=color*100+x-run
                    local pool=pools[key]
                    if not pool then
                        pool={width=(x-run)*10,color=color,used=0}; pools[key]=pool
                    end
                    local n=(used[key] or 0)+1; used[key]=n
                    if not pool[n] then nextid=nextid+1; pool[n]=nextid end
                    p.rect(pool[n],160+run*10,120+y*10,pool.width,10,colors[color+1])
                end
                run=x; color=symbol
            end
        end
    end
    for key,pool in pairs(pools) do
        local n=used[key] or 0
        for i=n+1,pool.used do
            p.rect(pool[i],-2000,-2000,pool.width,10,colors[pool.color+1])
        end
        pool.used=n
    end
    S.dirty_draw=false
end
local function respond(n,body,err)
    seq=n; data=tostring(body); status=err and 1 or 0
    if #data>MAX then data='Response exceeds 64 KiB'; status=1 end
    page=0; count=math.max(1,math.ceil(#data/CHUNK)); checksum=W.adler(data)
    mode='show'; rheld=push(117); show(true)
end
local function handle(raw)
    local op,num,name,body=raw:match('^([^\n]*)\n([^\n]*)\n([^\n]*)\n(.*)$')
    local n=tonumber(num)
    if not n or not num:match('^%d+$') or n<1 or n>4294967295 or n%1~=0 then
        respond(0,'Invalid request sequence',true); return
    end
    local ok,result=pcall(function()
        if op=='L' then
            assert(name=='' and body=='','LIST has no filename or data')
            local rows={}
            for _,f in ipairs(names) do
                local value=p.read(f)
                if value then rows[#rows+1]=f..'\t'..#value..'\n' end
            end
            return table.concat(rows)
        end
        assert(valid(name),'Invalid filename (maximum 64 characters)')
        if op=='G' then
            assert(body=='','GET has no data')
            local value,why=p.read(name); assert(value,why or 'File not found')
            assert(#value<=MAX,'File exceeds 64 KiB'); return value
        elseif op=='P' then
            assert(name~=INDEX,'Reserved index filename')
            assert(#body<=MAX,'File exceeds 64 KiB')
            p.write(name,body)
            if add(name) then
                local indexed,why=pcall(save_index)
                if not indexed then
                    seen[name]=nil; names[#names]=nil
                    error('File saved; index write failed: '..tostring(why))
                end
            end
            return 'OK\t'..name..'\t'..#body..'\n'
        end
        error('Unknown request operation')
    end)
    respond(n,result,not ok)
end
local function receive()
    restore(); S.receive=nil
    assert(p.receive(),'Receive unavailable')
    respond(0,'READY',false); mode='receive'
end
local function leave()
    S.receive=nil; restore(); S.press={}; S.suppress_until=e:now()+500
    e:setEventHandler{onEnterFrame='seedsow_editor_frame'}
    _G.ss_ftp_frame=nil; p.openEditor()
end
_G.ss_ftp_frame=function()
    local now=e:now()
    if S.input:held('L') then
        ltime=ltime or now
        if mode=='receive' then S.receive=nil; restore(); mode='show' end
        if now-ltime>=1500 then leave(); return end
        e:tag{'flip'}; return
    elseif ltime then
        ltime=nil; arm=now+350; mode='arm'
    end
    if mode=='arm' then
        if now>=arm then receive() end
    elseif mode=='receive' then
        local r=S.receive
        if r then original() end
        if r and r.complete and not r.error then
            S.receive=nil; restore(); handle(r.source)
        elseif not r or r.error or not S.receive then
            S.receive=nil; restore(); respond(0,r and r.error or 'Receive stopped',true)
        else
            return -- The original receive frame has already flipped the screen.
        end
    else
        local r=push(117)
        if r and not rheld then page=(page+1)%count; show() end
        rheld=r
    end
    e:tag{'flip'}
end
-- Persist the seed directory once; uploads extend it with successfully saved files.
save_index()
e:setEventHandler{onEnterFrame='ss_ftp_frame'}
receive()
