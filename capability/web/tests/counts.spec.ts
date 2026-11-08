import {test,expect} from '@playwright/test';
import {familyUse,matchCount} from '../src/counts';

const backups=['backup','backups'] as const,copies=['public copy','public copies'] as const;

test('match counts agree in number at 0, 1 and many',()=>{
 expect(matchCount(0,0,backups)).toBe('0 of your 0 backups match.');
 expect(matchCount(0,1,backups)).toBe('0 of your 1 backup match.');
 expect(matchCount(1,1,backups)).toBe('1 of your 1 backup matches.');
 expect(matchCount(1,3,backups)).toBe('1 of your 3 backups matches.');
 expect(matchCount(2,3,copies)).toBe('2 of your 3 public copies match.');
});

test('family use counts agree in number at 1 and many',()=>{
 expect(familyUse(1,backups)).toBe('1 of your backups uses one of these.');
 expect(familyUse(2,backups)).toBe('2 of your backups use one of these.');
 expect(familyUse(1,copies)).toBe('1 of your public copies uses one of these.');
 expect(familyUse(5,copies)).toBe('5 of your public copies use one of these.');
});
