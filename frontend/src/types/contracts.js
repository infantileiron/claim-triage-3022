/** @typedef {{id:number,code:string,amount:number,distance:number,holiday:boolean,label:0|1|null,note:string,claimant:?string,merchant:?string,occurred_at:?string}} Claim */
/** @typedef {{id:number,name:string,threshold:number,state:string,result:object}} Training */
/** @typedef {{id:number,claim_id:number,training_id:number,score:number,decision:string,status:string}} Review */
/** @typedef {{id:number,limit_amount:number,state:string,error:string,group_count:number,over_limit_count:number,eligible_count:number,incomplete_count:number,trigger:string,incomplete:Array,groups?:Array}} Screening */
/** @typedef {{id:number,screening_id:number,group_key:string,total_amount:number,member_count:number,status:string,decision:string,note:string,stale:boolean,stale_reason:string,review_version:number,members?:Array,chain?:Array,events?:Array}} CorrelationGroup */
