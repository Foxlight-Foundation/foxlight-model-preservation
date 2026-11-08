/** A noun as [singular, plural], for example ['backup','backups']. */
export type Noun=readonly [string,string];

/** "1 of your 3 backups matches", "2 of your 3 backups match", "0 of your 1 backup match". */
export function matchCount(matching:number,total:number,noun:Noun):string{
 return `${matching} of your ${total} ${total===1?noun[0]:noun[1]} ${matching===1?'matches':'match'}.`;
}

/** "1 of your backups uses one of these", "2 of your backups use one of these". */
export function familyUse(uses:number,noun:Noun):string{
 return `${uses} of your ${noun[1]} ${uses===1?'uses':'use'} one of these.`;
}
