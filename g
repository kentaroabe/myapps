<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>PDF/画像のぼかし処理</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
        }
        #drop-zone {
            border: 2px dashed #ccc;
            padding: 20px;
            text-align: center;
            margin-bottom: 20px;
        }
        #drop-zone.dragover {
            border-color: #000;
            background-color: #eee;
        }
        #pixelSizeControl {
            display: flex;
            align-items: center;
            margin-bottom: 10px;
        }
        #pixelSizeControl button {
            font-size: 24px;
            padding: 10px 20px;
            border: 1px solid #ccc;
            background-color: #f9f9f9;
            cursor: pointer;
            transition: background-color 0.3s;
        }
        #pixelSizeControl button:hover {
            background-color: #e0e0e0;
        }
        #pixelSizeControl input {
            width: 60px;
            text-align: center;
            margin: 0 10px;
        }
        #canvas {
            display: none;
        }
    </style>
</head>
      <a href="../" style="margin: 20px; padding: 10px 20px; background-color: #6c757d; color: white; border: none; border-radius: 5px; text-decoration: none; display: inline-block;">アプリリストに戻る</a>

<body>
    <h1>PDF/画像のモザイク処理</h1>
    <label for="pixelSize">ピクセルサイズ:</label>
    <div id="pixelSizeControl">
        <button id="decreasePixelSize">-</button>
        <input type="number" id="pixelSize" value="2" min="1" step="1">
        <button id="increasePixelSize">+</button>
    </div>

    <div id="drop-zone">ここにPDFまたは画像ファイルをドラッグ&ドロップしてください</div>
    <input type="file" id="file-upload" accept="application/pdf,image/jpeg,image/png" multiple style="display: none;" />

    <canvas id="canvas"></canvas>

    <!-- 必要なライブラリを読み込み -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.14.305/pdf.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>

    <script>
        const fileUpload = document.getElementById('file-upload');
        const dropZone = document.getElementById('drop-zone');
        const pixelSizeInput = document.getElementById('pixelSize');
        const increasePixelSizeBtn = document.getElementById('increasePixelSize');
        const decreasePixelSizeBtn = document.getElementById('decreasePixelSize');
        const canvas = document.getElementById('canvas');
        const ctx = canvas.getContext('2d');

        // 画像の最大幅と高さを設定（解像度を下げる）
        const MAX_WIDTH = 800;
        const MAX_HEIGHT = 800;

        dropZone.addEventListener('dragover', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZone.classList.add('dragover');
        });

        dropZone.addEventListener('dragleave', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZone.classList.remove('dragover');
        });

        dropZone.addEventListener('drop', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZone.classList.remove('dragover');
            const files = event.dataTransfer.files;
            handleFiles(files);
        });

        dropZone.addEventListener('click', () => {
            fileUpload.click();
        });

        fileUpload.addEventListener('change', (event) => {
            const files = event.target.files;
            handleFiles(files);
        });

        async function handleFiles(files) {
            const images = [];
            for (const file of files) {
                if (file.type === 'application/pdf') {
                    // PDFを処理
                    const pdfImages = await processPDF(file);
                    images.push(...pdfImages);
                } else if (file.type.startsWith('image/')) {
                    // 画像ファイルを処理
                    const imgData = await processImageFile(file);
                    images.push(imgData);
                } else {
                    alert(`${file.name} はサポートされていないファイル形式です。スキップします。`);
                }
            }

            if (images.length > 0) {
                // すべての画像を処理した後、PDFを生成
                await generatePDF(images);
            } else {
                alert('有効なファイルがありません。');
            }
        }

        async function processPDF(file) {
            const images = [];
            const fileReader = new FileReader();
            return new Promise((resolve, reject) => {
                fileReader.onload = async function() {
                    try {
                        const typedarray = new Uint8Array(this.result);
                        const pdf = await pdfjsLib.getDocument(typedarray).promise;
                        const numPages = pdf.numPages;

                        for (let pageNum = 1; pageNum <= numPages; pageNum++) {
                            const page = await pdf.getPage(pageNum);
                            // スケールを小さくして解像度を下げる
                            const viewport = page.getViewport({ scale: 1 }); // ここを調整
                            const tempCanvas = document.createElement('canvas');
                            const tempCtx = tempCanvas.getContext('2d');
                            tempCanvas.width = viewport.width;
                            tempCanvas.height = viewport.height;

                            const renderContext = {
                                canvasContext: tempCtx,
                                viewport: viewport
                            };

                            await page.render(renderContext).promise;

                            // 解像度をさらに下げるためにリサイズ
                            const resizedDataUrl = resizeImage(tempCanvas);

                            // キャンバスに再描画
                            const img = new Image();
                            await new Promise((resolveImg) => {
                                img.onload = () => {
                                    canvas.width = img.width;
                                    canvas.height = img.height;
                                    ctx.drawImage(img, 0, 0);
                                    resolveImg();
                                };
                                img.src = resizedDataUrl;
                            });

                            // モザイク処理を適用
                            await applyMosaic();

                            // キャンバスから画像データを取得
                            const imgDataUrl = canvas.toDataURL('image/jpeg', 0.8);

                            images.push({
                                dataUrl: imgDataUrl,
                                name: `${file.name} - ページ ${pageNum}`
                            });
                        }
                        resolve(images);
                    } catch (error) {
                        console.error(`Error processing ${file.name}:`, error);
                        alert(`${file.name} の処理中にエラーが発生しました。`);
                        resolve(images);
                    }
                };
                fileReader.readAsArrayBuffer(file);
            });
        }

        async function processImageFile(file) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = function(e) {
                    const img = new Image();
                    img.onload = async function() {
                        // 解像度を下げるためにリサイズ
                        const resizedDataUrl = resizeImage(img);

                        // キャンバスに再描画
                        const resizedImg = new Image();
                        await new Promise((resolveImg) => {
                            resizedImg.onload = () => {
                                canvas.width = resizedImg.width;
                                canvas.height = resizedImg.height;
                                ctx.drawImage(resizedImg, 0, 0);
                                resolveImg();
                            };
                            resizedImg.src = resizedDataUrl;
                        });

                        // モザイク処理を適用
                        await applyMosaic();

                        // キャンバスから画像データを取得
                        const imgDataUrl = canvas.toDataURL('image/jpeg', 0.8);

                        resolve({
                            dataUrl: imgDataUrl,
                            name: file.name
                        });
                    };
                    img.src = e.target.result;
                };
                reader.readAsDataURL(file);
            });
        }

        function resizeImage(image) {
            let width = image.width;
            let height = image.height;

            // アスペクト比を保ちながらサイズを縮小
            if (width > MAX_WIDTH) {
                height *= MAX_WIDTH / width;
                width = MAX_WIDTH;
            }

            if (height > MAX_HEIGHT) {
                width *= MAX_HEIGHT / height;
                height = MAX_HEIGHT;
            }

            const offscreenCanvas = document.createElement('canvas');
            offscreenCanvas.width = width;
            offscreenCanvas.height = height;
            const offscreenCtx = offscreenCanvas.getContext('2d');
            offscreenCtx.drawImage(image, 0, 0, width, height);

            return offscreenCanvas.toDataURL('image/jpeg', 0.7); // 画質をさらに下げる
        }

        function applyMosaic() {
            return new Promise((resolve) => {
                const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                const pixelSize = parseInt(pixelSizeInput.value, 10) || 8;

                for (let y = 0; y < canvas.height; y += pixelSize) {
                    for (let x = 0; x < canvas.width; x += pixelSize) {
                        const pixelIndex = (y * canvas.width + x) * 4;
                        const red = imageData.data[pixelIndex];
                        const green = imageData.data[pixelIndex + 1];
                        const blue = imageData.data[pixelIndex + 2];
                        const alpha = imageData.data[pixelIndex + 3];

                        for (let dy = 0; dy < pixelSize; dy++) {
                            for (let dx = 0; dx < pixelSize; dx++) {
                                const nx = x + dx;
                                const ny = y + dy;
                                if (nx < canvas.width && ny < canvas.height) {
                                    const index = (ny * canvas.width + nx) * 4;
                                    imageData.data[index] = red;
                                    imageData.data[index + 1] = green;
                                    imageData.data[index + 2] = blue;
                                    imageData.data[index + 3] = alpha;
                                }
                            }
                        }
                    }
                }

                ctx.putImageData(imageData, 0, 0);
                resolve();
            });
        }

        async function generatePDF(images) {
            const { jsPDF } = window.jspdf;
            const pdf = new jsPDF();

            for (let i = 0; i < images.length; i++) {
                const imgData = images[i];
                const img = new Image();

                await new Promise((resolve) => {
                    img.onload = function() {
                        const imgWidth = pdf.internal.pageSize.getWidth();
                        const imgHeight = (img.height * imgWidth) / img.width;
                        pdf.addImage(imgData.dataUrl, 'JPEG', 0, 0, imgWidth, imgHeight, undefined, 'FAST');

                        if (i < images.length - 1) {
                            pdf.addPage();
                        }

                        resolve();
                    };
                    img.src = imgData.dataUrl;
                });
            }

            const baseName = 'モザイク処理済み';
            pdf.save(`${baseName}.pdf`);
        }

        increasePixelSizeBtn.addEventListener('click', () => {
            let pixelSize = parseInt(pixelSizeInput.value, 10);
            pixelSize += 1;
            pixelSizeInput.value = pixelSize;
        });

        decreasePixelSizeBtn.addEventListener('click', () => {
            let pixelSize = parseInt(pixelSizeInput.value, 10);
            if (pixelSize > 1) {
                pixelSize -= 1;
                pixelSizeInput.value = pixelSize;
            }
        });
    </script>
</body>
</html>
