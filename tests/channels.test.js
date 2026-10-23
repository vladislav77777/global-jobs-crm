const {test} = require('node:test');
const assert = require('node:assert/strict');
const {sendTelegram, sendWebsite, replyTelegram, deliver, uploadVerifiedFile} = require('../agent/channels');
const allowed = async()=>({allowed:true});

test('test mode never calls an adapter', async()=>{
  let calls=0;
  const result=await deliver({request:{},bridge:{reserve:async()=>({state:'dry_run'})},adapter:async()=>{calls++}});
  assert.equal(calls,0); assert.equal(result.state,'dry_run');
});
test('disabled Telegram switch blocks native MCP calls', async()=>{
  let calls=0;
  await assert.rejects(sendTelegram({request:{},callTool:async()=>calls++,assertAllowed:async()=>{throw Error('Off')}}));
  assert.equal(calls,0);
});
test('Telegram uses connected native tools and returns a real message ID', async()=>{
  const result=await sendTelegram({request:{account:'test-account',recipient:123,text:'Hello',cv_url:'https://example.invalid/cv'},
    callTool:async(name,args)=>{if(name==='get_me')return {id:'test-account'};assert.equal(name,'send_message');assert.match(args.message,/CV:/);return {structuredContent:{result:'{"id":42}'}}},assertAllowed:allowed});
  assert.equal(result.remote_id,'42');
});
test('unsupported required field is found before any fill', async()=>{
  let calls=0;
  await assert.rejects(sendWebsite({request:{form_reviewed:true,submit_label:'Apply',fields:[
    {label:'Name',kind:'text',required:true,value:'Example'}, {label:'Agree',kind:'consent',required:true,value:true}]},
    tab:{getAXState:async()=>{calls++}},assertAllowed:allowed}));
  assert.equal(calls,0);
});
test('web form fills known fields, skips optional data and verifies receipt', async()=>{
  let log=[];
  const locator={fill:async x=>log.push(x),click:async()=>log.push('Submit')};
  const tab={getAXState:async()=> 'Example form',playwright:{getByLabel:()=>locator,getByRole:()=>locator}};
  const result=await sendWebsite({request:{form_reviewed:true,recipient:'https://example.invalid/apply',submit_label:'Apply',fields:[
    {label:'Name',kind:'text',required:true,value:'Example'}, {label:'Phone',kind:'text',required:false,value:'123'}]},
    tab,assertAllowed:allowed,verifyForm:async()=>true,readReceipt:async()=>({remote_id:'application-1'})});
  assert.deepEqual(log,['Example','Submit']); assert.equal(result.remote_id,'application-1');
});
test('switch disabled before Submit prevents clicking it', async()=>{
  let checks=0,clicks=0;
  const tab={getAXState:async()=> 'Form',playwright:{getByLabel:()=>({fill:async()=>{}}),getByRole:()=>({click:async()=>clicks++})}};
  await assert.rejects(sendWebsite({request:{form_reviewed:true,submit_label:'Apply',fields:[{label:'Name',kind:'text',required:true,value:'Example'}]},
    tab,assertAllowed:async()=>{if(++checks===3)throw Error('Off');return {allowed:true}},verifyForm:async()=>true,readReceipt:async()=>({remote_id:'x'})}));
  assert.equal(clicks,0);
});
test('unknown result is persisted and never retried', async()=>{
  let states=[];
  await assert.rejects(deliver({request:{},bridge:{reserve:async()=>({state:'reserved'}),begin:async()=>({}),
    finish:async(t,r)=>states.push(r)},adapter:async()=>{throw Error('Timeout')}}));
  assert.deepEqual(states,[null]);
});
test('altered attachment is rejected before opening a file chooser', async()=>{
  let calls=0;
  await assert.rejects(uploadVerifiedFile({field:{value:require('node:path').resolve(__dirname,'../examples/synthetic-cv.txt'),file_sha256:'wrong'},
    tab:{playwright:{waitForEvent:async()=>calls++}},assertAllowed:allowed}));
  assert.equal(calls,0);
});
test('wrong Telegram account never sends a message', async()=>{
  let sends=0;
  await assert.rejects(sendTelegram({request:{account:'expected',recipient:123,text:'Hello',cv_url:'https://example.invalid/cv'},
    assertAllowed:allowed,callTool:async name=>{if(name==='get_me')return {id:'different'};sends++;return {id:42}}}));
  assert.equal(sends,0);
});
test('a false or empty permission result cannot send', async()=>{
  for(const verdict of [false,undefined,{allowed:false}]){
    let calls=0;
    await assert.rejects(sendTelegram({request:{},callTool:async()=>calls++,assertAllowed:async()=>verdict}));
    assert.equal(calls,0);
  }
});
