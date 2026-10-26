import light from './branding/sovereign-light-navbar.svg';
import dark from './branding/sovereign-dark-navbar.svg';
import lightSymbol from './branding/sovereign-light-symbol.svg';
import darkSymbol from './branding/sovereign-dark-symbol.svg';

/** Approved outlined identity; theme changes never alter its proportions. */
export function SovereignBrand({theme,symbol=false}:{theme:'night'|'noon';symbol?:boolean}) {
  return <img className={symbol?'sovereign-symbol':'sovereign-logo'} src={symbol?(theme==='night'?darkSymbol:lightSymbol):(theme==='night'?dark:light)} width={symbol?180:550} height={symbol?180:132} alt={symbol?'':'Sovereign'}/>;
}
