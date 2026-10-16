// Selected Bootstrap outline vectors + the Font Awesome branding logo. No fonts or CDN.
const fs = require('node:fs');
const path = require('node:path');
const { minify } = require('terser');
const names = {
  archive:'archive', back:'arrow-left', chevron:'chevron-down', database:'database',
  key:'key', plus:'plus-lg', close:'x-lg', reload:'arrow-repeat', image:'image',
  shield:'shield-check', stop:'stop', stopFilled:'stop-fill', send:'arrow-up', play:'play', trash:'trash2-fill',
  terminal:'terminal', list:'list-ul', read:'file-earmark-text', search:'search',
  edit:'pencil', context:'layers', cost:'currency-dollar', model:'cpu',
  copy:'copy', check:'check-lg', error:'exclamation-triangle', robot:'robot', wallet:'wallet2',
};
const bootstrapRoot = path.dirname(require.resolve('bootstrap-icons/package.json'));
const icons = Object.fromEntries(Object.entries(names).map(([name, symbol]) => {
  if(symbol.endsWith('-fill') && !['trash','stopFilled'].includes(name)) throw new Error('Use outline Bootstrap icons');
  const raw = fs.readFileSync(path.join(bootstrapRoot,'icons',symbol+'.svg'),'utf8');
  const body = raw.slice(raw.indexOf('>')+1,raw.lastIndexOf('</svg>')).trim().replace(/>\s+</g,'><');
  return [name, [16, 16, body]];
}));
const {icon:[width,height,,,data]} = require('@fortawesome/free-solid-svg-icons/faHexagonNodes').faHexagonNodes;
icons.nodes = [width,height,'<path d="'+data+'"/>'];
const media = path.join(__dirname, '../media');
const version = require('@fortawesome/free-solid-svg-icons/package.json').version;
const notice = `/*! Bootstrap Icons (MIT), Font Awesome Free ${version} logo (CC BY 4.0). See BootstrapIcons.LICENSE.txt and FontAwesome.LICENSE.txt. */`;
const source = `${notice}
(function(root){
  const icons=${JSON.stringify(icons)};
  function svg(name, classes='') {
    if (!Object.prototype.hasOwnProperty.call(icons,name)) throw new Error('Unknown icon');
    if (!/^[a-zA-Z0-9 _-]*$/.test(classes)) throw new Error('Invalid icon class');
    const [width,height,data]=icons[name];
    return '<svg class="agent-icon '+(name==='nodes'?'fa-icon':'bi-icon')+' icon-'+name+(classes?' '+classes:'')+'" viewBox="0 0 '+width+' '+height+'" aria-hidden="true" focusable="false">'+data+'</svg>';
  }
  const api=Object.freeze({svg});
  if(typeof module==='object' && module.exports) module.exports=api;
  else root.AgentIcons=api;
})(globalThis);`;
(async () => {
  const result=await minify(source,{compress:{passes:3},mangle:true,format:{comments:/^!/}});
  fs.writeFileSync(path.join(media,'icons.min.js'),result.code+'\n');
  fs.copyFileSync(path.join(path.dirname(require.resolve('@fortawesome/free-solid-svg-icons/package.json')),'LICENSE.txt'),path.join(media,'FontAwesome.LICENSE.txt'));
  fs.copyFileSync(path.join(bootstrapRoot,'LICENSE'),path.join(media,'BootstrapIcons.LICENSE.txt'));
  const render=require(path.join(media,'icons.min.js')).svg;
  for(const name of ['robot','key','wallet']) {
    for(const [theme,color] of [['light','#424242'],['dark','#c5c5c5']]) {
      const svg=render(name).replace('<svg ',`<svg xmlns="http://www.w3.org/2000/svg" fill="${color}" `);
      fs.writeFileSync(path.join(media,`bi-${name}-${theme}.svg`),notice.replace('/*!','<!--').replace('*/','-->')+svg+'\n');
    }
  }
  console.log(Object.keys(icons).length+' selected icons: '+Buffer.byteLength(result.code)+' bytes JS');
})();
