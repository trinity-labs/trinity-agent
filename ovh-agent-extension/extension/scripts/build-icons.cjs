// Build only the selected Font Awesome Free vectors; no runtime library/font/CDN.
const fs = require('node:fs');
const path = require('node:path');
const { minify } = require('terser');
const names = {
  nodes:'faHexagonNodes', archive:'faBoxArchive', back:'faArrowLeft', chevron:'faChevronDown', database:'faDatabase',
  key:'faKey', plus:'faPlus', close:'faXmark', reload:'faArrowsRotate', image:'faImage',
  shield:'faShieldHalved', stop:'faStop', send:'faArrowUp', play:'faPlay', trash:'faTrashCan',
  terminal:'faTerminal', list:'faList', read:'faFileLines', search:'faMagnifyingGlass',
  edit:'faPen', context:'faLayerGroup', cost:'faDollarSign', model:'faMicrochip',
  copy:'faCopy', check:'faCheck', error:'faTriangleExclamation', robot:'faRobot', wallet:'faWallet',
};
const icons = Object.fromEntries(Object.entries(names).map(([name, symbol]) => {
  const { icon: [width, height, , , data] } = require('@fortawesome/free-solid-svg-icons/' + symbol)[symbol];
  if (typeof data !== 'string') throw new Error('Expected a single solid path: ' + symbol);
  return [name, [width, height, data]];
}));
const media = path.join(__dirname, '../media');
const version = require('@fortawesome/free-solid-svg-icons/package.json').version;
const notice = `/*! Font Awesome Free ${version} by Fonticons, Inc. — Icons CC BY 4.0; see FontAwesome.LICENSE.txt. */`;
const source = `${notice}
(function(root){
  const icons=${JSON.stringify(icons)};
  function svg(name, classes='') {
    if (!Object.prototype.hasOwnProperty.call(icons,name)) throw new Error('Unknown icon');
    if (!/^[a-zA-Z0-9 _-]*$/.test(classes)) throw new Error('Invalid icon class');
    const [width,height,data]=icons[name];
    return '<svg class="fa-icon'+(classes?' '+classes:'')+'" viewBox="0 0 '+width+' '+height+'" aria-hidden="true" focusable="false"><path d="'+data+'"/></svg>';
  }
  const api=Object.freeze({svg});
  if(typeof module==='object' && module.exports) module.exports=api;
  else root.AgentIcons=api;
})(globalThis);`;
(async () => {
  const result=await minify(source,{compress:{passes:3},mangle:true,format:{comments:/^!/}});
  fs.writeFileSync(path.join(media,'icons.min.js'),result.code+'\n');
  fs.copyFileSync(path.join(path.dirname(require.resolve('@fortawesome/free-solid-svg-icons/package.json')),'LICENSE.txt'),path.join(media,'FontAwesome.LICENSE.txt'));
  const render=require(path.join(media,'icons.min.js')).svg;
  for(const name of ['robot','key','wallet']) {
    for(const [theme,color] of [['light','#424242'],['dark','#c5c5c5']]) {
      const svg=render(name).replace('<svg ',`<svg xmlns="http://www.w3.org/2000/svg" fill="${color}" `);
      fs.writeFileSync(path.join(media,`fa-${name}-${theme}.svg`),notice.replace('/*!','<!--').replace('*/','-->')+svg+'\n');
    }
  }
  console.log(Object.keys(icons).length+' Font Awesome icons: '+Buffer.byteLength(result.code)+' bytes JS');
})();
