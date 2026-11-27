({texts, includeText}) => {
  const norm = s => s.replace(/\s+/gu, " ").trim().toLocaleLowerCase("ro");
  const block = "address,article,aside,blockquote,dd,div,dt,figcaption,footer,form,h1,h2,h3,h4,h5,h6,header,li,main,nav,p,pre,section,td,th,summary";
  const groups = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, chars = 0, truncated = false;
  while ((n = walker.nextNode())) {
    const el = n.parentElement;
    if (!el || el.closest("script,style,template,noscript")) continue;
    chars += n.length;
    if (chars > 300000) { truncated = true; break; }
    const key = el.closest(block) || document.body;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(n);
  }

  function inspectRange(node, start, end) {
    const el = node.parentElement;
    const own = getComputedStyle(el);
    if (own.visibility === "hidden" || own.visibility === "collapse")
      return ["hidden", "visibility:" + own.visibility];
    if (parseFloat(own.fontSize) === 0) return ["hidden", "font-size:0"];
    if (own.color === "transparent" || /rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(own.color))
      return ["hidden", "transparent text color"];
    let uncertain = false;
    const clips = [];
    for (let p = el; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.display === "none") return ["hidden", "display:none"];
      if (Number(s.opacity) === 0) return ["hidden", "opacity:0"];
      if (s.contentVisibility === "hidden") return ["hidden", "content-visibility:hidden"];
      if (s.contentVisibility === "auto" || s.clipPath !== "none" ||
          (s.clip !== "auto" && s.clip !== "") || (s.maskImage && s.maskImage !== "none") ||
          (s.filter && s.filter !== "none")) uncertain = true;
      if (p !== document.body && p !== document.documentElement &&
          /^(hidden|clip)$/.test(s.overflowX + "") ||
          p !== document.body && p !== document.documentElement && /^(hidden|clip)$/.test(s.overflowY + "")) {
        clips.push({r:p.getBoundingClientRect(), x:/^(hidden|clip)$/.test(s.overflowX),
                    y:/^(hidden|clip)$/.test(s.overflowY)});
      }
    }
    const range = document.createRange();
    range.setStart(node,start); range.setEnd(node,end);
    const rects = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0);
    if (!rects.length) return uncertain ? ["indeterminate","no text box; deferred or complex style"] : ["hidden","no text layout box"];
    const intersections = rects.map(r => {
      let l=r.left,t=r.top,rr=r.right,b=r.bottom;
      for(const c of clips) {
        if(c.x){l=Math.max(l,c.r.left);rr=Math.min(rr,c.r.right);}
        if(c.y){t=Math.max(t,c.r.top);b=Math.min(b,c.r.bottom);}
      }
      return rr>l && b>t;
    });
    if (!intersections.some(Boolean)) return ["hidden","clipped by overflow"];
    if (uncertain) return ["indeterminate","complex or deferred CSS"];
    if (!intersections.every(Boolean)) return ["partial","some text boxes clipped"];
    return ["visible","computed style and nonzero text layout"];
  }

  const answers = texts.map(query => ({text:query, occurrences:0, states:{visible:0,hidden:0,partial:0,indeterminate:0}, examples:[]}));
  for(const [blockEl,nodes] of groups) {
    // Preserve inline splits while mapping normalized characters to DOM offsets.
    let value="", mapping=[];
    for(const node of nodes) {
      for(let i=0;i<node.length;) {
        const cp=String.fromCodePoint(node.data.codePointAt(i)), end=i+cp.length;
        if(/\s/u.test(cp)) {
          if(value && !value.endsWith(" ")){value+=" ";mapping.push(null);}
        } else {
          const folded=cp.toLocaleLowerCase("ro");
          value+=folded;
          for(let j=0;j<folded.length;j++) mapping.push({node,start:i,end});
        }
        i=end;
      }
    }
    for(const a of answers) {
      const q=norm(a.text);
      let pos=0;
      while((pos=value.indexOf(q,pos))!==-1) {
        if(a.occurrences>=1000){truncated=true;break;}
        const parts=new Map();
        for(const m of mapping.slice(pos,pos+q.length)) if(m) {
          const prev=parts.get(m.node);
          parts.set(m.node,prev?[prev[0],m.end]:[m.start,m.end]);
        }
        const states=[...parts].map(([node,r])=>inspectRange(node,...r));
        const kinds=new Set(states.map(s=>s[0]));
        const state=kinds.has("indeterminate")?"indeterminate":kinds.size>1||kinds.has("partial")?"partial":states[0]?.[0]||"indeterminate";
        a.occurrences++; a.states[state]++;
        if(a.examples.length<5) a.examples.push({state, reasons:[...new Set(states.map(s=>s[1]))],
          tag:blockEl.tagName.toLowerCase(), id:(blockEl.id||"").slice(0,100),
          classes:(blockEl.getAttribute("class")||"").slice(0,200)});
        pos+=q.length;
      }
    }
  }
  for(const a of answers) {
    a.observed=a.states.visible?"visible":a.states.indeterminate||truncated?"indeterminate":
      a.states.partial?"partial":a.states.hidden?"hidden":"not_found";
  }
  const bodyText=includeText?document.body.innerText:"";
  return {matches:answers, scan_truncated:truncated, title:document.title,
    inner_text:bodyText.slice(0,12000), inner_text_truncated:bodyText.length>12000,
    headings:includeText?[...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].slice(0,35)
      .map(h=>({level:Number(h.tagName[1]),text:h.innerText.slice(0,300)})):[]};
}