// Optional Windows CPU inference worker. Reads selected gbrp16le frames on stdin.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include "onnxruntime_c_api.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

using File = std::unique_ptr<FILE, decltype(&fclose)>;
using Features = std::array<double, 6>;

struct Picture {
    std::vector<float> pooled;
    std::array<double, 3> means{};
    Features features{};
};

static double median(std::vector<double> x, double p) {
    std::sort(x.begin(), x.end());
    const double i = (x.size() - 1) * p;
    return x[size_t(i)] + (i - size_t(i)) * (x[size_t(std::ceil(i))] - x[size_t(i)]);
}

static Picture read_picture(int w, int h) {
    const size_t area = size_t(w) * h;
    std::vector<unsigned short> pixels(3 * area);
    if (fread(pixels.data(), sizeof(unsigned short), pixels.size(), stdin) != pixels.size())
        throw std::runtime_error("Incomplete RGB frame from decoder");
    const int pw = std::max(1, w / 8), ph = std::max(1, h / 8);
    Picture pic;
    pic.pooled.resize(size_t(3) * pw * ph);
    std::vector<double> luma;
    const int channel[3] = {2, 0, 1}; // planar GBR to RGB
    for (int c = 0; c < 3; c++) {
        const unsigned short* plane = pixels.data() + channel[c] * area;
        uint64_t sum = 0, sampled = 0;
        for (size_t i = 0; i < area; i++) sum += plane[i];
        pic.means[c] = double(sum) / (65535.0 * area);
        for (int y = 0; y < h; y += 8)
            for (int x = 0; x < w; x += 8) sampled += plane[size_t(y) * w + x];
        pic.features[c] = double(sampled) / (65535.0 * ((w + 7) / 8) * ((h + 7) / 8));
        for (int y = 0; y < ph; y++)
            for (int x = 0; x < pw; x++) {
                unsigned block = 0;
                for (int dy = 0; dy < 8; dy++)
                    for (int dx = 0; dx < 8; dx++)
                        block += plane[size_t(std::min(h-1, 8*y + dy)) * w + std::min(w-1, 8*x + dx)];
                pic.pooled[(c * ph + y) * pw + x] = float(block / (64.0 * 65535.0));
            }
    }
    for (int y = 0; y < h; y += 8)
        for (int x = 0; x < w; x += 8) {
            const size_t i = size_t(y) * w + x;
            luma.push_back((pixels[i] + pixels[i + area] + pixels[i + 2*area]) / (3.0 * 65535));
        }
    const double p[3] = {0.50, 0.90, 0.99};
    for (int c = 0; c < 3; c++) pic.features[3+c] = median(luma, p[c]);
    return pic;
}

static size_t representative(const std::vector<Picture>& shots) {
    Features center{};
    for (size_t k = 0; k < center.size(); k++) {
        std::vector<double> values;
        for (const auto& shot : shots) values.push_back(shot.features[k]);
        center[k] = median(values, 0.50);
    }
    size_t best = 0;
    double best_distance = HUGE_VAL;
    for (size_t i = 0; i < shots.size(); i++) {
        double distance = 0;
        for (size_t k = 0; k < center.size(); k++) {
            const double d = shots[i].features[k] - center[k];
            distance += d * d;
        }
        if (distance < best_distance) { best = i; best_distance = distance; }
    }
    return best;
}

class Predictor {
    const OrtApi* api;
    OrtEnv* env = nullptr;
    OrtSessionOptions* options = nullptr;
    OrtSession* session = nullptr;
    OrtMemoryInfo* memory = nullptr;
    void check(OrtStatus* status) {
        if (status) {
            std::string message = api->GetErrorMessage(status);
            api->ReleaseStatus(status);
            throw std::runtime_error(message);
        }
    }
public:
    explicit Predictor(const std::wstring& folder) {
        auto lib = LoadLibraryExW((folder + L"\\onnxruntime.dll").c_str(), nullptr,
                                  LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!lib) throw std::runtime_error("ONNX Runtime DLL could not be loaded");
        using Entry = const OrtApiBase* (ORT_API_CALL *)();
        auto entry = reinterpret_cast<Entry>(GetProcAddress(lib, "OrtGetApiBase"));
        if (!entry || !(api = entry()->GetApi(ORT_API_VERSION)))
            throw std::runtime_error("Incompatible ONNX Runtime");
        check(api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "Cinema LUT", &env));
        check(api->CreateSessionOptions(&options));
        check(api->SetIntraOpNumThreads(options, 4));
        check(api->SetSessionGraphOptimizationLevel(options, ORT_ENABLE_ALL));
        check(api->CreateSession(env, (folder+L"\\cinema.onnx").c_str(), options, &session));
        check(api->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &memory));
    }
    ~Predictor() {
        api->ReleaseMemoryInfo(memory);
        api->ReleaseSession(session);
        api->ReleaseSessionOptions(options);
        api->ReleaseEnv(env);
    }
    std::array<float,15> predict(Picture& pic, int w, int h) {
        OrtValue *input = nullptr, *output = nullptr;
        int64_t shape[] = {1,3,std::max(1,h/8),std::max(1,w/8)};
        check(api->CreateTensorWithDataAsOrtValue(memory, pic.pooled.data(), pic.pooled.size()*sizeof(float),
               shape, 4, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &input));
        const char* in = "prior";
        const char* out = "weights";
        auto status = api->Run(session,nullptr,&in,&input,1,&out,1,&output);
        api->ReleaseValue(input);
        check(status);
        float* values = nullptr;
        check(api->GetTensorMutableData(output, reinterpret_cast<void**>(&values)));
        std::array<float,15> weights;
        std::copy_n(values,15,weights.begin());
        api->ReleaseValue(output);
        return weights;
    }
};

int wmain(int argc, wchar_t** argv) {
    try {
        if (argc != 6) throw std::runtime_error("Expected model folder, plan, output, width, height");
        _setmode(_fileno(stdin), _O_BINARY);
        int w = _wtoi(argv[4]), h = _wtoi(argv[5]);
        if (w < 1 || h < 1 || w > 16384 || h > 16384 || int64_t(w)*h > 35000000)
            throw std::runtime_error("Invalid model input raster");
        File plan(_wfopen(argv[2], L"rt"), &fclose), output(_wfopen(argv[3], L"wb"), &fclose);
        if (!plan || !output) throw std::runtime_error("Cannot open plan or output");
        Predictor net(argv[1]);
        int count;
        while (fscanf(plan.get(), "%d", &count) == 1) {
            if (count < 1 || count > 5) throw std::runtime_error("Invalid number of candidate pictures");
            std::vector<Picture> candidates;
            for (int i = 0; i < count; i++) candidates.push_back(read_picture(w,h));
            size_t index = representative(candidates);
            auto& pic = candidates[index];
            auto weights = net.predict(pic,w,h);
            fprintf(output.get(), "%u", unsigned(index));
            for (double m : pic.means) fprintf(output.get(), " %.10g", m);
            for (float v : weights) fprintf(output.get(), " %.9g", double(v));
            if (fprintf(output.get(), "\n") < 0 || fflush(output.get()) != 0)
                throw std::runtime_error("Cannot write model output");
        }
        if (!feof(plan.get()) || fgetc(stdin) != EOF) throw std::runtime_error("Shot-plan/frame count mismatch");
        return 0;
    } catch (const std::exception& error) {
        fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
