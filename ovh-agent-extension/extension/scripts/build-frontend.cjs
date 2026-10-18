'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {minify}=require('terser');
const source=path.resolve(__dirname,'../media');

async function buildFrontend(destination=source) {
  fs.mkdirSync(destination,{recursive:true});
  const sizes=[];
  for(const name of ['chat','i18n','safe-html']) {
    const file=name+'.js', code=fs.readFileSync(path.join(source,file),'utf8');
    // Do not mangle top-level names: the plain scripts intentionally share APIs.
    // Source maps retain readable sources for contributors debugging a VSIX.
    const result=await minify({[file]:code},{compress:{passes:2},mangle:true,
      sourceMap:{filename:name+'.min.js',url:name+'.min.js.map',includeSources:true},
      format:{comments:/^!/}});
    fs.writeFileSync(path.join(destination,name+'.min.js'),result.code+'\n');
    fs.writeFileSync(path.join(destination,name+'.min.js.map'),result.map+'\n');
    sizes.push({file,source:Buffer.byteLength(code),minified:Buffer.byteLength(result.code)+1});
  }
  return sizes;
}
if(require.main===module) {
  const args=process.argv.slice(2);
  if(args.length && (args.length!==2 || args[0]!=='--out'))throw Error('Usage: build-frontend.cjs [--out directory]');
  buildFrontend(args[1]?path.resolve(args[1]):source).then(rows=>console.log(JSON.stringify(rows,null,2))).catch(error=>{console.error(error);process.exitCode=1;});
}
module.exports={buildFrontend};
