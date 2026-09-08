import { generateKeyPairSync } from 'node:crypto';

const { privateKey } = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
const jwk = privateKey.export({ format: 'jwk' });
const dec = s => Buffer.from(s.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4 - s.length % 4) % 4), 'base64');
const enc = b => Buffer.from(b).toString('base64url');
const publicRaw = Buffer.concat([Buffer.from([4]), dec(jwk.x), dec(jwk.y)]);

console.log('\nVAPID_SERVER_PUBLIC_KEY');
console.log(enc(publicRaw));
console.log('\nVAPID_SERVER_PRIVATE_KEY');
console.log(jwk.d);
console.log('\n私鑰只放進 GitHub / Cloudflare Secret，不要 commit、不要截圖公開。\n');
