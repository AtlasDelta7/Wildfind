/*
WildFind - make data/map.js, the outline map behind the Saved map.

Not part of the fetch: run once by hand, and only again to change the map.
The coastline is Natural Earth's 1:10m countries (public domain), as packaged
in the world-atlas npm module:

    npm install world-atlas@2.0.2 topojson-client@3.1.0
    node tools/make-map.js data/map.js

Coordinates are projected to the British National Grid with the same
formulas the app uses for grid references, so pins and coast line up, and are
stored as whole hundreds of metres, each point as the step from the last.
*/
"use strict";
const fs = require("fs");
const topojson = require("topojson-client");
const world = require("world-atlas/countries-10m.json");

const rad = d => d * Math.PI / 180, deg = r => r * 180 / Math.PI;

// Copied from WildFind.html: WGS84 -> OSGB36 (Helmert), then Transverse Mercator.
function wgs84ToOsgb36(lat, lon){
  const a0=6378137, f=1/298.257223563, e2w=2*f-f*f;
  const phi=rad(lat), lam=rad(lon);
  const nu=a0/Math.sqrt(1-e2w*Math.sin(phi)**2);
  const x=nu*Math.cos(phi)*Math.cos(lam), y=nu*Math.cos(phi)*Math.sin(lam), z=nu*(1-e2w)*Math.sin(phi);
  const tx=-446.448, ty=125.157, tz=-542.060, s=20.4894e-6,
        rx=rad(-0.1502/3600), ry=rad(-0.2470/3600), rz=rad(-0.8421/3600);
  const x2=tx+x*(1+s)-y*rz+z*ry, y2=ty+x*rz+y*(1+s)-z*rx, z2=tz-x*ry+y*rx+z*(1+s);
  const a=6377563.396, b=6356256.909, e2=(a*a-b*b)/(a*a);
  const p=Math.sqrt(x2*x2+y2*y2);
  let phi2=Math.atan2(z2,p);
  for(let i=0;i<20;i++){ const nu2=a/Math.sqrt(1-e2*Math.sin(phi2)**2); phi2=Math.atan2(z2+e2*nu2*Math.sin(phi2),p); }
  return { lat: deg(phi2), lon: deg(Math.atan2(y2,x2)) };
}
function osgb36ToEN(lat, lon){
  const a=6377563.396, b=6356256.909, e2=(a*a-b*b)/(a*a);
  const F0=0.9996012717, phi0=rad(49), lam0=rad(-2), E0=400000, N0=-100000;
  const phi=rad(lat), lam=rad(lon), n=(a-b)/(a+b);
  const cp=Math.cos(phi), sp=Math.sin(phi), tp=Math.tan(phi);
  const nu=a*F0/Math.sqrt(1-e2*sp*sp), rho=a*F0*(1-e2)/Math.pow(1-e2*sp*sp,1.5), eta2=nu/rho-1;
  const dP=phi-phi0, sP=phi+phi0;
  const M=b*F0*((1+n+1.25*n*n+1.25*n*n*n)*dP-(3*n+3*n*n+2.625*n*n*n)*Math.sin(dP)*Math.cos(sP)
    +(1.875*n*n+1.875*n*n*n)*Math.sin(2*dP)*Math.cos(2*sP)-(35/24)*n*n*n*Math.sin(3*dP)*Math.cos(3*sP));
  const I=M+N0, II=nu/2*sp*cp, III=nu/24*sp*cp**3*(5-tp*tp+9*eta2), IIIA=nu/720*sp*cp**5*(61-58*tp*tp+tp**4),
        IV=nu*cp, V=nu/6*cp**3*(nu/rho-tp*tp), VI=nu/120*cp**5*(5-18*tp*tp+tp**4+14*eta2-58*tp*tp*eta2);
  const dL=lam-lam0;
  return [E0+IV*dL+V*dL**3+VI*dL**5, I+II*dL**2+III*dL**4+IIIA*dL**6];
}
const project = ([lon, lat]) => { const g = wgs84ToOsgb36(lat, lon); return osgb36ToEN(g.lat, g.lon); };

// What the map holds, in grid metres: all of Ireland to Belgium and the
// Netherlands, Brittany to Shetland. The app lets the view centre wander a
// little less far than this, so the cut edges stay out of sight.
const BOX = { x0: -320000, x1: 1000000, y0: -220000, y1: 1320000 };
// 0 home: the UK, Isle of Man and Channel Islands. 1 Ireland, drawn paler.
// 2 the Continent, cut off at the edge of BOX, so drawn as a coastline only.
const CLASS = { "826": 0, "833": 0, "831": 0, "832": 0, "372": 1, "250": 2, "056": 2, "528": 2 };
const TOLERANCE = 60;   // metres; Natural Earth 10m is coarser than this anyway

function clip(ring){     // Sutherland-Hodgman against BOX
  const edges = [
    [p => p[0] >= BOX.x0, (a, b) => at(a, b, 0, BOX.x0)],
    [p => p[0] <= BOX.x1, (a, b) => at(a, b, 0, BOX.x1)],
    [p => p[1] >= BOX.y0, (a, b) => at(a, b, 1, BOX.y0)],
    [p => p[1] <= BOX.y1, (a, b) => at(a, b, 1, BOX.y1)],
  ];
  function at(a, b, k, v){ const t = (v - a[k]) / (b[k] - a[k]); return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]; }
  let out = ring;
  for (const [inside, cut] of edges){
    const src = out; out = [];
    for (let i = 0; i < src.length; i++){
      const a = src[i], b = src[(i + 1) % src.length];
      if (inside(b)){ if (!inside(a)) out.push(cut(a, b)); out.push(b); }
      else if (inside(a)) out.push(cut(a, b));
    }
    if (!out.length) break;
  }
  return out;
}
function simplify(pts, tol){   // Douglas-Peucker
  if (pts.length < 4) return pts;
  const keep = new Uint8Array(pts.length); keep[0] = keep[pts.length - 1] = 1;
  const stack = [[0, pts.length - 1]];
  while (stack.length){
    const [i, j] = stack.pop();
    const [ax, ay] = pts[i], [bx, by] = pts[j];
    const dx = bx - ax, dy = by - ay, L = Math.hypot(dx, dy) || 1;
    let best = -1, bd = tol;
    for (let k = i + 1; k < j; k++){
      const d = Math.abs(dy * (pts[k][0] - ax) - dx * (pts[k][1] - ay)) / L;
      if (d > bd){ bd = d; best = k; }
    }
    if (best > 0){ keep[best] = 1; stack.push([i, best], [best, j]); }
  }
  return pts.filter((_, k) => keep[k]);
}
function encode(pts){
  const out = []; let px = 0, py = 0;
  for (const [x, y] of pts){
    const qx = Math.round(x / 100), qy = Math.round(y / 100);
    if (out.length && qx === px && qy === py) continue;
    out.push(qx - px, qy - py); px = qx; py = qy;
  }
  return out;
}

const rings = [];
let points = 0;
for (const f of topojson.feature(world, world.objects.countries).features){
  const id = String(f.id);
  if (!(id in CLASS)) continue;
  const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
  for (const poly of polys){
    const outer = poly[0].map(project);       // holes are lakes, not needed
    const c = clip(outer);
    if (c.length < 3) continue;
    const s = simplify(c, TOLERANCE);   // open: first and last are neighbours
    if (s.length < 3) continue;
    const e = encode(s);
    if (e.length < 6) continue;
    rings.push([CLASS[id], e]);
    points += e.length / 2;
  }
}
// Neighbours first, so the home coast is drawn on top.
rings.sort((a, b) => b[0] - a[0]);
const body = "/* Outline map for the Saved map. Made by tools/make-map.js from Natural Earth\n" +
  "   1:10m countries (public domain), British National Grid, hundreds of metres,\n" +
  "   each ring [0 UK, 1 Ireland or 2 the Continent, [x,y, dx,dy, ...]]. */\n" +
  "window.WF_MAP={unit:100,rings:" + JSON.stringify(rings) + "};\n";
fs.writeFileSync(process.argv[2] || "map.js", body, "ascii");
console.log(rings.length + " rings, " + points + " points, " + body.length + " bytes");
