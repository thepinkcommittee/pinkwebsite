(function(){
	"use strict";

	// 4-level grayscale palette
	var PALETTE = [0, 104, 184, 255]; // #000000, #686868, #b8b8b8, #ffffff
	var TARGET_DITHER_PIXEL_SIZE = 1.75; // target on-screen pixel block size in CSS px

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

	function drawImageCover(ctx, img, sourceW, sourceH, destW, destH){
		var sourceRatio = sourceW / sourceH;
		var destRatio = destW / destH;
		var sx = 0;
		var sy = 0;
		var sw = sourceW;
		var sh = sourceH;

		if (sourceRatio > destRatio){
			// Source is wider than destination: crop left/right.
			sw = sourceH * destRatio;
			sx = (sourceW - sw) / 2;
		}else if (sourceRatio < destRatio){
			// Source is taller than destination: crop top/bottom.
			sh = sourceW / destRatio;
			sy = (sourceH - sh) / 2;
		}

		ctx.drawImage(img, sx, sy, sw, sh, 0, 0, destW, destH);
	}

	function ditherImageElement(img){
		var naturalW = img.naturalWidth || img.width;
		var naturalH = img.naturalHeight || img.height;
		if (!naturalW || !naturalH) return;

		var displayW = Math.round(img.clientWidth || 0);
		if (!displayW && img.parentElement) {
			displayW = Math.round(img.parentElement.clientWidth || 0);
		}
		if (!displayW) {
			displayW = Math.round(Math.min(naturalW, document.body.clientWidth || naturalW));
		}

		var displayH = Math.round(img.clientHeight || 0);
		if (!displayH) {
			displayH = Math.round(parseFloat(window.getComputedStyle(img).height) || 0);
		}
		if (!displayH) {
			displayH = Math.round(displayW * (naturalH / naturalW));
		}

		displayW = Math.max(1, displayW);
		displayH = Math.max(1, displayH);

		var ditherW = Math.max(1, Math.round(displayW / TARGET_DITHER_PIXEL_SIZE));
		var ditherH = Math.max(1, Math.round(displayH / TARGET_DITHER_PIXEL_SIZE));

		var ditherCanvas = document.createElement('canvas');
		ditherCanvas.width = ditherW;
		ditherCanvas.height = ditherH;
		var ctx = ditherCanvas.getContext('2d', { willReadFrequently: true });
		drawImageCover(ctx, img, naturalW, naturalH, ditherW, ditherH);
		var imgData = ctx.getImageData(0, 0, ditherW, ditherH);
		var d = imgData.data;
		
		// Convert to grayscale first
		var gray = new Uint8ClampedArray(ditherW * ditherH);
		for (var i=0, p=0; i<d.length; i+=4, p++){
			var r = d[i], g = d[i+1], b = d[i+2];
			gray[p] = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
		}
		
		gray = floydSteinbergGrayscale(ditherW, ditherH, gray);
		
		// Convert back to RGBA
		for (var q=0, j=0; q<gray.length; q++, j+=4){
			var val = gray[q];
			d[j] = val;     // R
			d[j+1] = val;   // G
			d[j+2] = val;   // B
			d[j+3] = 255;   // A
		}
		
		ctx.putImageData(imgData, 0, 0);

		var outputCanvas = document.createElement('canvas');
		outputCanvas.width = displayW;
		outputCanvas.height = displayH;
		var outCtx = outputCanvas.getContext('2d');
		outCtx.imageSmoothingEnabled = false;
		outCtx.drawImage(ditherCanvas, 0, 0, ditherW, ditherH, 0, 0, displayW, displayH);
		try{
			img.src = outputCanvas.toDataURL('image/png');
		}catch(e){
			outputCanvas.width = displayW;
			outputCanvas.height = displayH;
			img.replaceWith(outputCanvas);
		}
	}

	function getCharacterWidth(referenceEl){
		var probe = document.createElement('span');
		probe.textContent = '0000000000';
		probe.style.position = 'absolute';
		probe.style.visibility = 'hidden';
		probe.style.whiteSpace = 'pre';
		probe.style.padding = '0';
		probe.style.margin = '0';
		var refStyle = window.getComputedStyle(referenceEl);
		probe.style.font = refStyle.font;
		document.body.appendChild(probe);
		var width = probe.getBoundingClientRect().width / 10;
		probe.remove();
		return width > 0 ? width : 8;
	}

	function longestWordLength(text){
		var words = (text || '').trim().split(/\s+/).filter(Boolean);
		var maxLen = 0;
		for (var i = 0; i < words.length; i++){
			if (words[i].length > maxLen) maxLen = words[i].length;
		}
		return maxLen;
	}

	function optimizeRowWhereWidth(row, config){
		var whereEl = row.querySelector('.where');
		if (!whereEl) return;

		var rowStyle = window.getComputedStyle(row);
		var gapPx = parseFloat(rowStyle.columnGap) || 0;
		var charPx = getCharacterWidth(row);
		var rootStyle = window.getComputedStyle(document.documentElement);
		var dateCh = parseFloat(rootStyle.getPropertyValue(config.dateVar)) || config.defaultDateCh;
		var rowWidthPx = row.getBoundingClientRect().width;
		var budgetPx = rowWidthPx - (dateCh * charPx) - (2 * gapPx);
		var budgetCh = Math.floor(budgetPx / charPx);

		if (!isFinite(budgetCh) || budgetCh <= config.minTitleCh) return;

		var locationText = (whereEl.textContent || '').replace(/[()]/g, ' ').trim();
		var minWhereCh = Math.max(config.minWhereCh, longestWordLength(locationText));
		var maxWhereCh = Math.min(config.maxWhereCh, budgetCh - config.minTitleCh);
		if (maxWhereCh < minWhereCh){
			minWhereCh = Math.max(1, maxWhereCh);
		}
		if (minWhereCh <= 0) return;

		var bestWhereCh = minWhereCh;
		var bestHeight = Number.POSITIVE_INFINITY;

		for (var whereCh = minWhereCh; whereCh <= maxWhereCh; whereCh++){
			row.style.setProperty('--where-col', whereCh + 'ch');
			var height = row.getBoundingClientRect().height;
			if (height < bestHeight - 0.01 || (Math.abs(height - bestHeight) <= 0.01 && whereCh < bestWhereCh)){
				bestHeight = height;
				bestWhereCh = whereCh;
			}
		}

		row.style.setProperty('--where-col', bestWhereCh + 'ch');
	}

	function optimizeRows(config){
		var rows = document.querySelectorAll(config.rowSelector);
		rows.forEach(function(row){ optimizeRowWhereWidth(row, config); });
	}

	function optimizeLayoutColumns(){
		optimizeRows({
			rowSelector: '.hack',
			dateVar: '--hack-date-col-width',
			defaultDateCh: 10,
			minTitleCh: 8,
			minWhereCh: 8,
			maxWhereCh: 40
		});

		optimizeRows({
			rowSelector: '#news .hack-item > div',
			dateVar: '--news-date-col-width',
			defaultDateCh: 19,
			minTitleCh: 8,
			minWhereCh: 8,
			maxWhereCh: 40
		});
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

		optimizeLayoutColumns();

		var resizeTimer = 0;
		window.addEventListener('resize', function(){
			window.clearTimeout(resizeTimer);
			resizeTimer = window.setTimeout(optimizeLayoutColumns, 120);
		});
	});
})(); 