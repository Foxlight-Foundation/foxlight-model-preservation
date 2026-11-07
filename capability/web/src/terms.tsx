import {listed} from './community-parts';
import {Callout,Consent,Pill} from './ui';

/** One licence a person accepts before keeping a model, as the host describes it. */
export interface LicenceTerms {license_id:string;name:string;family:string|null;summary:string;requires_acceptance:boolean;references:{members:string[];reference:string;url:string|null}[]}
/** The terms review on a receive plan; `accept` lists licences that need a tick. */
export interface TermsReview {definition:number;licences:LicenceTerms[];accept:string[]}

/** The ids a start must send, or nothing when no licence needs acceptance. */
export const acceptedTerms=(terms:TermsReview|undefined|null,accepted:boolean):{accepted_terms?:string[]}=>terms&&terms.accept.length>0&&accepted?{accepted_terms:terms.accept}:{};
/** Whether this review still waits for "I accept these terms". */
export const termsPending=(terms:TermsReview|undefined|null,accepted:boolean)=>Boolean(terms&&terms.accept.length>0&&!accepted);

/**
 * Hugging Face-style gate: each licence with its plain summary and a link to
 * its full text, then one explicit "I accept these terms" when any licence has
 * use restrictions or conditions to pass on. Plain permissive licences need none.
 */
export function TermsAcceptance({terms,accepted,onChange}:{terms:TermsReview;accepted:boolean;onChange:(value:boolean)=>void}){
 const gated=terms.licences.filter(item=>item.requires_acceptance);
 return <div className="sk-terms" aria-label="Licence terms">
  <ul className="sk-terms-list">{terms.licences.map(item=><li key={item.license_id}>
   <span className="sk-terms-head"><strong>{item.name}</strong>{item.license_id&&<code>{item.license_id}</code>}{item.requires_acceptance&&<Pill tone="info">Needs your acceptance</Pill>}</span>
   <span>{item.summary}</span>
   {item.references.map((reference,index)=><small key={index}>{reference.url?<a href={reference.url} target="_blank" rel="noopener noreferrer">Read the full terms{reference.reference.startsWith('https://')?'':` (${reference.reference}, carried with the model)`}</a>:<>Full terms: {reference.reference}, carried with the model</>}</small>)}
  </li>)}</ul>
  {gated.length>0?<section className="sk-terms-accept" aria-label="Terms to accept"><h4>Terms to accept</h4><Consent checked={accepted} onChange={onChange} note="As on Hugging Face, these terms are accepted before the model is kept. Your acceptance is recorded with this copy.">I accept the {listed(gated.map(item=>item.name))}.</Consent></section>:<p className="sk-hint">These licences ask nothing extra of you beyond keeping their notices.</p>}
  <Callout tone="info">This describes the licences in plain words; it isn’t legal advice. You’re responsible for following them where you live.</Callout>
 </div>;
}
