(function(){
	"use strict";

	// 4-level grayscale palette
	var PALETTE = [0, 104, 184, 255]; // #000000, #686868, #b8b8b8, #ffffff

	function findClosestColor(gray) {
		var closest = PALETTE[0];
		var minDist = Math.abs(gray - closest);
		for (var i = 1; i < PALETTE.length; i++) {
			var dist = Math.abs(gray - PALETTE[i]);
			if (dist < minDist) {
				minDist = dist;
				closest = PALETTE[i];
			}
		}
		return closest;
	}

	var DIV = 16.0; // Floyd–Steinberg divisor

	function floydSteinbergGrayscale(width, height, data){
		var buf = new Float32Array(data.length);
		for (var i=0;i<data.length;i++) buf[i] = data[i];
		function idx(x,y){ return y*width + x; }
		for (var y=0;y<height;y++){
			for (var x=0;x<width;x++){
				var i2 = idx(x,y);
				var oldv = buf[i2];
				var newv = findClosestColor(oldv);
				buf[i2] = newv;
				var err = oldv - newv;
				// Floyd–Steinberg neighbors
				var nbrs = [
					[x+1,y,7],
					[x-1,y+1,3],[x,y+1,5],[x+1,y+1,1]
				];
				for (var n=0;n<nbrs.length;n++){
					var nx = nbrs[n][0], ny = nbrs[n][1], w = nbrs[n][2];
					if (nx>=0 && nx<width && ny>=0 && ny<height){
						buf[idx(nx,ny)] += err * (w / DIV);
					}
				}
			}
		}
		var out = new Uint8ClampedArray(buf.length);
		for (var t=0;t<buf.length;t++) out[t] = Math.round(buf[t]);
		return out;
	}

	function ditherImageElement(img){
		var w = img.naturalWidth || img.width;
		var h = img.naturalHeight || img.height;
		if (!w || !h) return;
		var canvas = document.createElement('canvas');
		canvas.width = w; canvas.height = h;
		var ctx = canvas.getContext('2d', { willReadFrequently: true });
		ctx.drawImage(img, 0, 0, w, h);
		var imgData = ctx.getImageData(0, 0, w, h);
		var d = imgData.data;
		
		// Convert to grayscale first
		var gray = new Uint8ClampedArray(w*h);
		for (var i=0, p=0; i<d.length; i+=4, p++){
			var r = d[i], g = d[i+1], b = d[i+2];
			gray[p] = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
		}
		
		gray = floydSteinbergGrayscale(w,h,gray);
		
		// Convert back to RGBA
		for (var q=0, j=0; q<gray.length; q++, j+=4){
			var val = gray[q];
			d[j] = val;     // R
			d[j+1] = val;   // G
			d[j+2] = val;   // B
			d[j+3] = 255;   // A
		}
		
		ctx.putImageData(imgData, 0, 0);
		try{
			img.src = canvas.toDataURL('image/png');
		}catch(e){
			canvas.width = w; canvas.height = h;
			img.replaceWith(canvas);
		}
	}

	document.addEventListener('DOMContentLoaded', function(){
		var list = document.querySelectorAll('img[data-dither="stucki-cmyk"], img[data-dither="floyd-cmyk"], img[data-dither="gray4"]');
		list.forEach(function(img){
			if (img.complete){
				ditherImageElement(img);
			}else{
				img.addEventListener('load', function(){ ditherImageElement(img); }, { once:true });
			}
		});
	});
})(); 