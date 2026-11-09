CSS_BASE = '''
:root{color-scheme:light;--ink:#14201c;--ink2:#40504a;--muted:#5b6a63;--ground:#edf1ee;--surface:#fff;--line:#d5ded9;--line2:#e6ebe8;
--brand:#0f5a46;--brand-hi:#0a4535;--tint:#e0efe8;--shell:#0d2f27;--danger:#b3261e;--r:8px;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--ground);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif;
-webkit-font-smoothing:antialiased}
::selection{background:#bfe3d2;color:var(--ink)}
a{color:var(--brand);text-underline-offset:3px}a:hover{color:var(--brand-hi)}
h1,h2,h3{text-wrap:balance;margin:0}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.skip{position:absolute;left:12px;top:-48px;background:#fff;color:var(--ink);padding:8px 14px;border-radius:6px;z-index:10;transition:top .15s}
.skip:focus{top:10px}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.top{background:var(--shell);color:#fff}
.top .in{max-width:1080px;margin:0 auto;padding:0 20px;display:flex;align-items:center;gap:28px;min-height:56px;flex-wrap:wrap}
.top .brand{display:inline-flex;align-items:center;gap:10px;font-size:16px;font-weight:650;letter-spacing:.02em;color:#fff;text-decoration:none;padding:14px 0}
.top .brand .logo{width:26px;height:26px;flex:none}
.top nav{display:flex;gap:6px;flex:1}
.top nav a{color:#cfe3da;text-decoration:none;padding:17px 12px 15px;border-bottom:2px solid transparent;font-weight:500}
.top nav a:hover{color:#fff}
.top nav a[aria-current=page]{color:#fff;border-bottom-color:#6fd8ae}
.top .who{display:flex;align-items:center;gap:12px;color:#cfe3da}
.top .who button{background:transparent;color:#fff;border:1px solid #4d7266;padding:5px 12px}
.top .who button:hover{background:#17463a}
.top :focus-visible{outline-color:#9be8c8}
main{max-width:1080px;margin:0 auto;padding:28px 20px 72px}
.head{display:flex;align-items:flex-end;gap:16px;margin-bottom:20px;flex-wrap:wrap}
.head .t{flex:1;min-width:260px}
.head h1{font-size:22px;line-height:1.3;font-weight:650}
.head p{margin:4px 0 0}
.muted{color:var(--muted)}.small{font-size:12.5px}
.mono,code{font-family:var(--mono);font-size:12.5px}
code{background:var(--line2);padding:1px 5px;border-radius:4px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:20px 22px;margin-bottom:20px}
.card>h2,.card>.ch>h2{font-size:15px;font-weight:650}
.card>h2{margin-bottom:12px}
.ch{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}.ch h2{flex:1}
.flush{padding:0;overflow:hidden}.flush .ch{padding:16px 22px 0}
button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;background:var(--brand);color:#fff;border:1px solid var(--brand);
border-radius:6px;padding:7px 16px;min-height:36px;cursor:pointer;font:inherit;font-weight:500;text-decoration:none;touch-action:manipulation;
transition:background-color .15s,border-color .15s,color .15s}
button:hover,.btn:hover{background:var(--brand-hi);border-color:var(--brand-hi);color:#fff}
button.ghost,.btn.ghost{background:#fff;color:var(--brand);border-color:#b7c9c0}
button.ghost:hover,.btn.ghost:hover{background:var(--tint);color:var(--brand-hi)}
button.danger{background:var(--danger);border-color:var(--danger)}button.danger:hover{background:#8f1d17;border-color:#8f1d17}
button.sm{min-height:30px;padding:3px 12px;font-size:13px}
button{white-space:nowrap}
button:disabled{opacity:.5;cursor:not-allowed}
button:disabled:hover{background:var(--brand);border-color:var(--brand)}
button.ghost:disabled:hover{background:#fff;border-color:#b7c9c0}
input[type=text],input[type=number],input[type=password],select{width:100%;min-height:36px;border:1px solid #aebdb5;border-radius:6px;padding:6px 10px;font:inherit;
background:#fff;color:var(--ink);color-scheme:light}
input::placeholder{color:#66756e}
input:hover,select:hover{border-color:#7f948a}
input:focus-visible,select:focus-visible{outline:0;border-color:var(--brand);box-shadow:0 0 0 3px rgba(15,90,70,.22)}
input[type=checkbox]{width:16px;height:16px;accent-color:var(--brand);margin:0}
label{font-weight:600;font-size:13px}
.field{display:grid;gap:5px;margin-bottom:14px}
.field>.hint{font-weight:400;color:var(--muted);font-size:12.5px;margin:0}
.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.grid3 .field{margin-bottom:0}
.check{display:inline-flex;align-items:center;gap:8px;font-weight:400;cursor:pointer;min-height:32px}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:11px 14px;border-top:1px solid var(--line2);font-size:13.5px;vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12.5px;border-top:0;background:#f6f8f6;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr:hover{background:#f6f9f7}
td .sub{color:var(--muted);font-size:12.5px;overflow-wrap:anywhere}
.tw td:first-child{min-width:150px}
td a{font-weight:500}
.tw time,.tw td>a:not(.mono){white-space:nowrap}
.empty{padding:34px 20px;text-align:center;color:var(--muted)}
.empty b{display:block;color:var(--ink2);margin-bottom:4px;font-size:14px}
.badge{display:inline-flex;align-items:center;gap:6px;padding:1px 10px;border-radius:999px;font-size:12.5px;font-weight:500;background:#e9edea;color:#46524c;white-space:nowrap}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.s-done{background:#dff2e6;color:#16663a}.s-failed{background:#fde5e2;color:#9d241b}
.s-running{background:#e1ebfb;color:#1a4f9e}.s-cancelled{background:#e9edea;color:#4b5851}.s-queued{background:#fcefd0;color:#7d5100}
.s-uploading{background:#e1ebfb;color:#1a4f9e}
.s-running::before{animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.s-running::before{animation:none}*{transition:none!important}}
.err{color:var(--danger);font-size:13px}
.bar{height:8px;background:#e1e9e4;border-radius:4px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:var(--brand);transition:width .2s}
'''

CSS_MORE = '''
.split{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:20px;align-items:start}
.split .card{margin-bottom:0}
.panel{position:sticky;top:16px}
.panel h2{margin-bottom:4px}
.pick{margin:12px 0 16px;padding:10px 12px;border-radius:8px;background:#f3f6f4;border:1px dashed #b3c3ba;min-height:56px}
.pick.on{background:var(--tint);border:1px solid #9cc7b4}
.pick b{display:block;word-break:break-all}
.scope{border:0;margin:0 0 14px;padding:0}
.scope legend{font-weight:600;font-size:13px;padding:0;margin-bottom:6px}
.scope .opt{display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;margin-bottom:8px;cursor:pointer;flex-wrap:wrap}
.scope .opt.on{border-color:#7fb09b;background:var(--tint)}
.scope .opt:not(.on) input[type=number]{opacity:.55}
.scope input[type=number]{width:76px}
.scope input[type=radio]{width:16px;height:16px;accent-color:var(--brand);margin:0}
iframe.report{display:block;width:100%;height:calc(100vh - 150px);min-height:560px;border:0;background:#fff}
.saved{margin:0 0 16px;padding:10px 12px;border-radius:8px;background:#f6f8f6;border:1px solid var(--line2);font-size:13px}
.saved.warn{background:#fdf1ef;border-color:#e6b9b3}
.saved .sv{display:flex;justify-content:space-between;gap:8px}
tr.row-pick{cursor:pointer}
tr.row-pick.sel{background:var(--tint)}
tr.row-pick.sel:hover{background:var(--tint)}
.seg{display:inline-flex;border:1px solid #aebdb5;border-radius:6px;overflow:hidden;background:#fff}
.seg button{background:#fff;color:var(--ink2);border:0;border-radius:0;min-height:32px;padding:4px 14px;font-weight:500}
.seg button+button{border-left:1px solid #aebdb5}
.seg button:hover{background:#f1f5f3;color:var(--ink)}
.seg button[aria-pressed=true]{background:var(--brand);color:#fff}
.steps{list-style:none;margin:0;padding:0;counter-reset:s}
.steps li{counter-increment:s;position:relative;padding:0 0 18px 38px}
.steps li:last-child{padding-bottom:0}
.steps li::before{content:counter(s);position:absolute;left:0;top:0;width:26px;height:26px;border-radius:50%;background:var(--tint);color:var(--brand);
font-weight:650;display:grid;place-items:center;font-size:13px}
.steps h3{font-size:14px;font-weight:650;margin-bottom:2px}
.cmd{position:relative;margin:8px 0 0}
.cmd pre{margin:0;background:#10231d;color:#e3efe9;border-radius:8px;padding:12px 78px 12px 14px;overflow:auto;font:12.5px/1.65 var(--mono);white-space:pre-wrap;word-break:break-all}
.cmd button{position:absolute;right:8px;top:8px;background:#1c3a31;border-color:#35574b;color:#e3efe9;min-height:28px;padding:2px 10px;font-size:12.5px}
.cmd button:hover{background:#27493e;border-color:#4d7266}
pre.log{background:#10231d;color:#dce9e2;border-radius:8px;padding:12px 14px;overflow:auto;max-height:320px;font:12.5px/1.65 var(--mono);white-space:pre-wrap;margin:0}
details.fold{border-top:1px solid var(--line2);padding-top:14px;margin-top:6px}
details.fold summary{cursor:pointer;font-weight:600;color:var(--ink2);padding:4px 0}
details.fold[open] summary{margin-bottom:12px}
.kv{display:flex;flex-wrap:wrap;gap:6px 22px;margin:10px 0 0;padding:0;list-style:none;color:var(--ink2)}
.kv li span{color:var(--muted)}
.stat{display:flex;gap:28px;flex-wrap:wrap;margin:0}
.stat div{min-width:90px}.stat dt{color:var(--muted);font-size:12.5px}.stat dd{margin:0;font-size:18px;font-weight:650;font-variant-numeric:tabular-nums}
.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.login{max-width:400px;margin:72px auto 0}
.login h1{font-size:20px;margin-bottom:6px}
@media (max-width:900px){.split{grid-template-columns:minmax(0,1fr)}.panel{position:static}}
@media (max-width:560px){main{padding:20px 14px 56px}.card{padding:16px}.card.flush{padding:0}.flush .ch{padding:14px 16px 0}.grid3{grid-template-columns:1fr}
.top .in{gap:6px 16px}.top nav{order:3;flex-basis:100%}.top nav a{padding:10px 10px 8px}}
'''

CSS = CSS_BASE + CSS_MORE
